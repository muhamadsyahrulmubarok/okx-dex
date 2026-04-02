"""Python worker process controlled by backend API runtime config."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import signal
import time
from typing import Any

import requests

from okx_bot.client import OkxClient, OkxCredentials
from okx_bot.config import BotConfig, load_config_dict
from okx_bot.control_api import post_json
from okx_bot.engine import BotRunner
from okx_bot.storage import PositionStore
from okx_bot.telegram import build_notifier


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class RuntimeConfigProvider:
    def __init__(self, base_url: str, timeout_seconds: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def fetch(self) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/api/runtime/config",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid runtime config payload")
        return payload


class WorkerApp:
    def __init__(self) -> None:
        self.api_base_url = os.getenv("CONTROL_API_BASE_URL", "http://127.0.0.1:8080")
        self.positions_db_path = os.getenv("POSITIONS_DB_PATH", "positions.db")
        self.okx_api_key = os.getenv("OKX_API_KEY", "")
        self.okx_secret_key = os.getenv("OKX_SECRET_KEY", "")
        self.okx_passphrase = os.getenv("OKX_PASSPHRASE", "")
        self.okx_base_url = os.getenv("BASE_URL", "https://www.okx.com")
        self.runtime = RuntimeConfigProvider(self.api_base_url)
        self.store = PositionStore(self.positions_db_path)
        self._stop = False

    def _build_bot_runtime(
        self, runtime_payload: dict[str, Any], runner: BotRunner | None
    ) -> tuple[BotConfig, OkxClient, bool, Any]:
        settings = runtime_payload.get("settings") or {}
        tokens = runtime_payload.get("tokens") or []
        bot_config_payload = {
            "max_active_trades": settings.get("max_active_trades", 6),
            "trade_amount_usd": settings.get("trade_amount_usd", 100),
            "poll_interval_seconds": settings.get("poll_interval_seconds", 10),
            "request_timeout_seconds": settings.get("request_timeout_seconds", 10),
            "max_retries": settings.get("max_retries", 3),
            "retry_backoff_seconds": settings.get("retry_backoff_seconds", 1.5),
            "tokens": tokens,
        }
        config = load_config_dict(bot_config_payload)

        simulated = _to_bool(settings.get("okx_simulated", False))
        credentials = OkxCredentials(
            api_key=self.okx_api_key,
            secret_key=self.okx_secret_key,
            passphrase=self.okx_passphrase,
            base_url=self.okx_base_url,
            simulated=simulated,
        )
        client = OkxClient(
            credentials=credentials,
            timeout_seconds=config.request_timeout_seconds,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
        )
        dry_run = _to_bool(settings.get("dry_run", True))
        notifier = build_notifier(
            enabled=_to_bool(settings.get("telegram_enabled", False)),
            bot_token=settings.get("telegram_bot_token"),
            chat_id=settings.get("telegram_chat_id"),
            timeout_seconds=float(settings.get("telegram_timeout_seconds", 10.0)),
            max_retries=int(settings.get("telegram_max_retries", 3)),
            retry_backoff_seconds=float(
                settings.get("telegram_retry_backoff_seconds", 1.5)
            ),
        )
        return config, client, dry_run, notifier

    def _emit_trade_event(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload["raw_order_json"] = str(payload.get("raw_order"))
        post_json(f"{self.api_base_url}/api/events/trade", payload, timeout=5.0)

    def _emit_error_event(self, event: dict[str, Any]) -> None:
        post_json(f"{self.api_base_url}/api/events/error", event, timeout=5.0)

    def _heartbeat(self, status: str) -> None:
        post_json(
            f"{self.api_base_url}/api/events/heartbeat",
            {"status": status, "pid": os.getpid(), "last_execution_at": utc_now_iso()},
            timeout=4.0,
        )

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        logging.getLogger("worker").info("Received signal %s; shutting down", signum)
        self._stop = True

    def run(self) -> None:
        logger = logging.getLogger("worker")
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        runner: BotRunner | None = None
        last_poll = 10
        logger.info("Worker started")

        while not self._stop:
            try:
                runtime_payload = self.runtime.fetch()
                bot_state = runtime_payload.get("bot_state") or {}
                if str(bot_state.get("status", "STOPPED")).upper() != "RUNNING":
                    self._heartbeat("STOPPED")
                    time.sleep(1.0)
                    continue

                config, client, dry_run, notifier = self._build_bot_runtime(
                    runtime_payload, runner
                )
                last_poll = config.poll_interval_seconds

                if runner is None:
                    runner = BotRunner(
                        config=config,
                        client=client,
                        store=self.store,
                        dry_run=dry_run,
                        notifier=notifier,
                        on_trade_event=self._emit_trade_event,
                        on_error_event=self._emit_error_event,
                    )
                else:
                    runner.update_runtime(
                        config=config,
                        client=client,
                        dry_run=dry_run,
                        notifier=notifier,
                    )

                runner.run_cycle()
                self._heartbeat("RUNNING")
                time.sleep(config.poll_interval_seconds)
            except Exception as exc:
                logger.exception("Worker loop failed")
                self._emit_error_event(
                    {
                        "symbol": "SYSTEM",
                        "message": f"Worker loop failed: {exc}",
                        "mode": "UNKNOWN",
                    }
                )
                time.sleep(max(2, min(last_poll, 30)))

        try:
            self._heartbeat("STOPPED")
        except Exception:
            logger.exception("Failed to send final heartbeat")
        logger.info("Worker stopped")


def main() -> None:
    log_level = os.getenv("WORKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    WorkerApp().run()


if __name__ == "__main__":
    main()
