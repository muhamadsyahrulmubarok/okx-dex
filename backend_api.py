"""Backend control API server with bot process management."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import signal
import subprocess
import threading
from typing import Any

from okx_bot.control_api import ControlStore, create_control_api


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class BotProcessManager:
    def __init__(self, store: ControlStore, worker_cmd: list[str]) -> None:
        self.store = store
        self.worker_cmd = worker_cmd
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None

    def _is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._is_running()
            pid = self._proc.pid if running and self._proc else None
        return {"running": running, "pid": pid}

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._is_running():
                state = self.store.update_bot_state(
                    status="RUNNING",
                    pid=self._proc.pid if self._proc else None,
                )
                return {"ok": True, "message": "already running", "state": state}

            self._proc = subprocess.Popen(self.worker_cmd)
            state = self.store.update_bot_state(
                status="RUNNING",
                pid=self._proc.pid,
                started_at=utc_now_iso(),
            )
            self.store.append_log("INFO", f"Bot worker started pid={self._proc.pid}")
            return {"ok": True, "message": "started", "state": state}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._is_running():
                state = self.store.update_bot_state(status="STOPPED", pid=None, stopped_at=utc_now_iso())
                return {"ok": True, "message": "already stopped", "state": state}

            assert self._proc is not None
            proc = self._proc
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            finally:
                self._proc = None

            state = self.store.update_bot_state(status="STOPPED", pid=None, stopped_at=utc_now_iso())
            self.store.append_log("INFO", "Bot worker stopped")
            return {"ok": True, "message": "stopped", "state": state}

    def restart(self) -> dict[str, Any]:
        self.stop()
        return self.start()


def main() -> None:
    log_level = os.getenv("BACKEND_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    backend_db_path = os.getenv("BACKEND_DB_PATH", "backend.db")
    api_host = os.getenv("BACKEND_HOST", "0.0.0.0")
    api_port = int(os.getenv("BACKEND_PORT", "8080"))
    python_bin = os.getenv("PYTHON_BIN", "python3")
    worker_script = os.getenv("WORKER_SCRIPT", "okx_worker.py")

    store = ControlStore(backend_db_path)
    manager = BotProcessManager(store=store, worker_cmd=[python_bin, worker_script])
    if _bool_env("BOT_AUTO_START", default=False):
        manager.start()

    app = create_control_api(
        store=store,
        start_bot_cb=manager.start,
        stop_bot_cb=manager.stop,
        restart_bot_cb=manager.restart,
        get_bot_runtime_status_cb=manager.status,
    )
    app.run(host=api_host, port=api_port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
