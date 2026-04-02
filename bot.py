"""Command-style runnable entrypoint for the rule-based OKX bot."""

from __future__ import annotations

import argparse
import logging
import os
import time

from dotenv import load_dotenv

from okx_bot.client import OkxClient, OkxCredentials
from okx_bot.config import load_config
from okx_bot.engine import BotRunner
from okx_bot.storage import PositionStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rule-based OKX API v5 trading bot")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to JSON bot config (default: config.json)",
    )
    parser.add_argument(
        "--db",
        default="positions.db",
        help="Path to SQLite DB for open positions (default: positions.db)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one cycle and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate signals without sending orders",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_credentials(*, require_auth: bool) -> OkxCredentials:
    api_key = os.getenv("OKX_API_KEY")
    secret_key = os.getenv("OKX_SECRET_KEY")
    passphrase = os.getenv("OKX_PASSPHRASE")
    base_url = os.getenv("BASE_URL", "https://www.okx.com")
    simulated = _bool_env("OKX_SIMULATED", default=False)

    missing = [
        name
        for name, value in (
            ("OKX_API_KEY", api_key),
            ("OKX_SECRET_KEY", secret_key),
            ("OKX_PASSPHRASE", passphrase),
        )
        if not value
    ]
    if require_auth and missing:
        missing_fields = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {missing_fields}")

    return OkxCredentials(
        api_key=api_key or "",
        secret_key=secret_key or "",
        passphrase=passphrase or "",
        base_url=base_url,
        simulated=simulated,
    )


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("bot")

    load_dotenv()
    config = load_config(args.config)
    credentials = load_credentials(require_auth=not args.dry_run)
    store = PositionStore(args.db)
    client = OkxClient(
        credentials,
        timeout_seconds=config.request_timeout_seconds,
        max_retries=config.max_retries,
        retry_backoff_seconds=config.retry_backoff_seconds,
    )
    runner = BotRunner(config, client, store, dry_run=args.dry_run)

    logger.info(
        "Starting bot | dry_run=%s | once=%s | max_active_trades=%s | trade_amount_usd=%.2f",
        args.dry_run,
        args.once,
        config.max_active_trades,
        config.trade_amount_usd,
    )

    if args.once:
        runner.run_cycle()
        logger.info("Open positions: %s", runner.positions_report())
        return

    while True:
        try:
            runner.run_cycle()
            logger.info("Open positions: %s", runner.positions_report())
            time.sleep(config.poll_interval_seconds)
        except KeyboardInterrupt:
            logger.info("Shutting down bot")
            return
        except Exception:
            logger.exception("Top-level bot loop failure")
            time.sleep(3)


if __name__ == "__main__":
    main()
