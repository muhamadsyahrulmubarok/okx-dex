"""Backend API + persistence for UI-controlled bot configuration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
import threading
from typing import Any, Callable

from flask_cors import CORS
from flask import Flask, jsonify, request
from flask_sock import Sock
import requests

from okx_bot.telegram import build_notifier

logger = logging.getLogger(__name__)


def load_bootstrap_tokens(path: str) -> list[dict[str, Any]]:
    if not path:
        return []
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        tokens = payload.get("tokens", [])
        if isinstance(tokens, list):
            return [token for token in tokens if isinstance(token, dict)]
    except Exception:
        logger.exception("Failed to load bootstrap tokens from %s", path)
    return []


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControlStore:
    """SQLite store for settings, tokens, trades, and logs."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    market_type TEXT NOT NULL DEFAULT 'CEX',
                    trade_amount_usd REAL NOT NULL,
                    max_active_trades INTEGER NOT NULL,
                    poll_interval_seconds INTEGER NOT NULL,
                    request_timeout_seconds REAL NOT NULL,
                    max_retries INTEGER NOT NULL,
                    retry_backoff_seconds REAL NOT NULL,
                    dex_base_url TEXT NOT NULL DEFAULT 'https://web3.okx.com',
                    dex_project_id TEXT,
                    dex_chain_id TEXT,
                    dex_quote_token_address TEXT,
                    dex_quote_token_decimals INTEGER NOT NULL DEFAULT 6,
                    dex_slippage REAL NOT NULL DEFAULT 0.005,
                    dex_wallet_address TEXT,
                    dex_private_key TEXT,
                    dex_rpc_url TEXT,
                    dex_live_execute INTEGER NOT NULL DEFAULT 0,
                    dex_price_probe_quote_amount REAL NOT NULL DEFAULT 1.0,
                    telegram_enabled INTEGER NOT NULL,
                    telegram_bot_token TEXT,
                    telegram_chat_id TEXT,
                    telegram_notify_positions INTEGER NOT NULL,
                    telegram_timeout_seconds REAL NOT NULL,
                    telegram_max_retries INTEGER NOT NULL,
                    telegram_retry_backoff_seconds REAL NOT NULL,
                    dry_run INTEGER NOT NULL,
                    okx_simulated INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    market_type TEXT,
                    buy_drop_pct REAL,
                    buy_drop_reference_price REAL,
                    buy_price REAL,
                    sell_profit_pct REAL,
                    sell_price REAL,
                    stop_loss_pct REAL,
                    dex_chain_id TEXT,
                    dex_token_address TEXT,
                    dex_quote_token_address TEXT,
                    dex_quote_token_decimals INTEGER,
                    dex_slippage REAL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    notional_usd REAL NOT NULL,
                    pnl_pct REAL,
                    reason TEXT,
                    mode TEXT NOT NULL,
                    raw_order_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    status TEXT NOT NULL,
                    pid INTEGER,
                    started_at TEXT,
                    stopped_at TEXT,
                    last_execution_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

            now = utc_now_iso()
            # Backward-compatible schema migrations for existing DBs.
            setting_columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)").fetchall()}
            token_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tokens)").fetchall()}
            if "market_type" not in setting_columns:
                conn.execute("ALTER TABLE settings ADD COLUMN market_type TEXT NOT NULL DEFAULT 'CEX'")
            if "dex_base_url" not in setting_columns:
                conn.execute(
                    "ALTER TABLE settings ADD COLUMN dex_base_url TEXT NOT NULL DEFAULT 'https://web3.okx.com'"
                )
            if "dex_project_id" not in setting_columns:
                conn.execute("ALTER TABLE settings ADD COLUMN dex_project_id TEXT")
            if "dex_chain_id" not in setting_columns:
                conn.execute("ALTER TABLE settings ADD COLUMN dex_chain_id TEXT")
            if "dex_quote_token_address" not in setting_columns:
                conn.execute("ALTER TABLE settings ADD COLUMN dex_quote_token_address TEXT")
            if "dex_quote_token_decimals" not in setting_columns:
                conn.execute(
                    "ALTER TABLE settings ADD COLUMN dex_quote_token_decimals INTEGER NOT NULL DEFAULT 6"
                )
            if "dex_slippage" not in setting_columns:
                conn.execute("ALTER TABLE settings ADD COLUMN dex_slippage REAL NOT NULL DEFAULT 0.005")
            if "dex_wallet_address" not in setting_columns:
                conn.execute("ALTER TABLE settings ADD COLUMN dex_wallet_address TEXT")
            if "dex_private_key" not in setting_columns:
                conn.execute("ALTER TABLE settings ADD COLUMN dex_private_key TEXT")
            if "dex_rpc_url" not in setting_columns:
                conn.execute("ALTER TABLE settings ADD COLUMN dex_rpc_url TEXT")
            if "dex_live_execute" not in setting_columns:
                conn.execute("ALTER TABLE settings ADD COLUMN dex_live_execute INTEGER NOT NULL DEFAULT 0")
            if "dex_price_probe_quote_amount" not in setting_columns:
                conn.execute(
                    "ALTER TABLE settings ADD COLUMN dex_price_probe_quote_amount REAL NOT NULL DEFAULT 1.0"
                )
            conn.execute(
                """
                INSERT OR IGNORE INTO settings (
                    id,
                    market_type,
                    trade_amount_usd,
                    max_active_trades,
                    poll_interval_seconds,
                    request_timeout_seconds,
                    max_retries,
                    retry_backoff_seconds,
                    dex_base_url,
                    dex_project_id,
                    dex_chain_id,
                    dex_quote_token_address,
                    dex_quote_token_decimals,
                    dex_slippage,
                    dex_wallet_address,
                    dex_private_key,
                    dex_rpc_url,
                    dex_live_execute,
                    dex_price_probe_quote_amount,
                    telegram_enabled,
                    telegram_bot_token,
                    telegram_chat_id,
                    telegram_notify_positions,
                    telegram_timeout_seconds,
                    telegram_max_retries,
                    telegram_retry_backoff_seconds,
                    dry_run,
                    okx_simulated,
                    created_at,
                    updated_at
                )
                VALUES (
                    1,
                    'CEX',
                    100.0,
                    6,
                    10,
                    10.0,
                    3,
                    1.5,
                    'https://web3.okx.com',
                    NULL,
                    NULL,
                    NULL,
                    6,
                    0.005,
                    NULL,
                    NULL,
                    NULL,
                    0,
                    1.0,
                    0,
                    NULL,
                    NULL,
                    0,
                    10.0,
                    3,
                    1.5,
                    1,
                    0,
                    ?,
                    ?
                )
                """,
                (now, now),
            )
            conn.execute(
                """
                UPDATE settings SET
                    market_type = COALESCE(market_type, 'CEX'),
                    dex_base_url = COALESCE(dex_base_url, 'https://web3.okx.com'),
                    dex_quote_token_decimals = COALESCE(dex_quote_token_decimals, 6),
                    dex_slippage = COALESCE(dex_slippage, 0.005),
                    dex_live_execute = COALESCE(dex_live_execute, 0),
                    dex_price_probe_quote_amount = COALESCE(dex_price_probe_quote_amount, 1.0)
                WHERE id = 1
                """
            )

            if "market_type" not in token_columns:
                conn.execute("ALTER TABLE tokens ADD COLUMN market_type TEXT")
            if "dex_chain_id" not in token_columns:
                conn.execute("ALTER TABLE tokens ADD COLUMN dex_chain_id TEXT")
            if "dex_token_address" not in token_columns:
                conn.execute("ALTER TABLE tokens ADD COLUMN dex_token_address TEXT")
            if "dex_quote_token_address" not in token_columns:
                conn.execute("ALTER TABLE tokens ADD COLUMN dex_quote_token_address TEXT")
            if "dex_quote_token_decimals" not in token_columns:
                conn.execute("ALTER TABLE tokens ADD COLUMN dex_quote_token_decimals INTEGER")
            if "dex_slippage" not in token_columns:
                conn.execute("ALTER TABLE tokens ADD COLUMN dex_slippage REAL")
            conn.execute(
                """
                UPDATE tokens SET
                    market_type = COALESCE(UPPER(TRIM(market_type)), 'CEX'),
                    dex_chain_id = NULLIF(TRIM(dex_chain_id), ''),
                    dex_token_address = NULLIF(TRIM(dex_token_address), ''),
                    dex_quote_token_address = NULLIF(TRIM(dex_quote_token_address), '')
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO bot_state (
                    id, status, pid, started_at, stopped_at, last_execution_at, updated_at
                )
                VALUES (1, 'STOPPED', NULL, NULL, NULL, NULL, ?)
                """,
                (now,),
            )
            existing_tokens = conn.execute("SELECT COUNT(1) AS cnt FROM tokens").fetchone()
            if int(existing_tokens["cnt"]) == 0:
                bootstrap_tokens = load_bootstrap_tokens(
                    os.getenv("BOOTSTRAP_CONFIG_PATH", "config.json")
                )
                for token in bootstrap_tokens:
                    symbol = str(token.get("symbol") or "").strip()
                    if not symbol:
                        continue
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO tokens (
                            symbol, market_type, buy_drop_pct, buy_drop_reference_price, buy_price,
                            sell_profit_pct, sell_price, stop_loss_pct,
                            dex_chain_id, dex_token_address, dex_quote_token_address,
                            dex_quote_token_decimals, dex_slippage,
                            is_active, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            symbol,
                            str(token.get("market_type") or "").strip().upper()
                            if token.get("market_type") is not None
                            else None,
                            token.get("buy_drop_pct"),
                            token.get("buy_drop_reference_price"),
                            token.get("buy_price"),
                            token.get("sell_profit_pct"),
                            token.get("sell_price"),
                            token.get("stop_loss_pct"),
                            token.get("dex_chain_id"),
                            token.get("dex_token_address"),
                            token.get("dex_quote_token_address"),
                            token.get("dex_quote_token_decimals"),
                            token.get("dex_slippage"),
                            now,
                            now,
                        ),
                    )
            conn.commit()

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        return {k: row[k] for k in row.keys()}

    def _redact_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        redacted = dict(settings)
        if "dex_private_key" in redacted:
            redacted["dex_private_key"] = None
        return redacted

    def get_settings(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            settings = self._row_to_dict(row)
        return self._redact_settings(settings)

    def _ensure_setting_columns(self) -> None:
        expected_columns = {
            "market_type": "TEXT NOT NULL DEFAULT 'CEX'",
            "dex_base_url": "TEXT NOT NULL DEFAULT 'https://web3.okx.com'",
            "dex_project_id": "TEXT",
            "dex_chain_id": "TEXT",
            "dex_quote_token_address": "TEXT",
            "dex_quote_token_decimals": "INTEGER NOT NULL DEFAULT 6",
            "dex_slippage": "REAL NOT NULL DEFAULT 0.005",
            "dex_wallet_address": "TEXT",
            "dex_private_key": "TEXT",
            "dex_rpc_url": "TEXT",
            "dex_live_execute": "INTEGER NOT NULL DEFAULT 0",
            "dex_price_probe_quote_amount": "REAL NOT NULL DEFAULT 1.0",
        }
        with self._lock, self._connect() as conn:
            rows = conn.execute("PRAGMA table_info(settings)").fetchall()
            existing = {str(row["name"]) for row in rows}
            for column, data_type in expected_columns.items():
                if column in existing:
                    continue
                conn.execute(f"ALTER TABLE settings ADD COLUMN {column} {data_type}")
            conn.execute(
                """
                UPDATE settings SET
                    market_type = COALESCE(market_type, 'CEX'),
                    dex_base_url = COALESCE(dex_base_url, 'https://web3.okx.com'),
                    dex_quote_token_decimals = COALESCE(dex_quote_token_decimals, 6),
                    dex_slippage = COALESCE(dex_slippage, 0.005),
                    dex_live_execute = COALESCE(dex_live_execute, 0),
                    dex_price_probe_quote_amount = COALESCE(dex_price_probe_quote_amount, 1.0)
                WHERE id = 1
                """
            )
            conn.commit()

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_setting_columns()
        allowed_fields = {
            "market_type",
            "trade_amount_usd",
            "max_active_trades",
            "poll_interval_seconds",
            "request_timeout_seconds",
            "max_retries",
            "retry_backoff_seconds",
            "dex_base_url",
            "dex_project_id",
            "dex_chain_id",
            "dex_quote_token_address",
            "dex_quote_token_decimals",
            "dex_slippage",
            "dex_wallet_address",
            "dex_private_key",
            "dex_rpc_url",
            "dex_live_execute",
            "dex_price_probe_quote_amount",
            "telegram_enabled",
            "telegram_bot_token",
            "telegram_chat_id",
            "telegram_notify_positions",
            "telegram_timeout_seconds",
            "telegram_max_retries",
            "telegram_retry_backoff_seconds",
            "dry_run",
            "okx_simulated",
        }
        updates = {k: payload[k] for k in payload if k in allowed_fields}
        if not updates:
            return self.get_settings()

        updates["updated_at"] = utc_now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        params = list(updates.values())
        params.append(1)

        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE settings SET {set_clause} WHERE id = ?", params)
            conn.commit()
        return self.get_settings()

    def _ensure_token_columns(self) -> None:
        expected_columns = {
            "market_type": "TEXT",
            "dex_chain_id": "TEXT",
            "dex_token_address": "TEXT",
            "dex_quote_token_address": "TEXT",
            "dex_quote_token_decimals": "INTEGER",
            "dex_slippage": "REAL",
        }
        with self._lock, self._connect() as conn:
            rows = conn.execute("PRAGMA table_info(tokens)").fetchall()
            existing = {str(row["name"]) for row in rows}
            for column, data_type in expected_columns.items():
                if column in existing:
                    continue
                conn.execute(f"ALTER TABLE tokens ADD COLUMN {column} {data_type}")
            conn.commit()

    def list_tokens(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tokens ORDER BY symbol ASC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def create_token(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        symbol = str(payload.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tokens (
                    symbol, market_type, buy_drop_pct, buy_drop_reference_price, buy_price,
                    sell_profit_pct, sell_price, stop_loss_pct,
                    dex_chain_id, dex_token_address, dex_quote_token_address,
                    dex_quote_token_decimals, dex_slippage,
                    is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    payload.get("market_type"),
                    payload.get("buy_drop_pct"),
                    payload.get("buy_drop_reference_price"),
                    payload.get("buy_price"),
                    payload.get("sell_profit_pct"),
                    payload.get("sell_price"),
                    payload.get("stop_loss_pct"),
                    payload.get("dex_chain_id"),
                    payload.get("dex_token_address"),
                    payload.get("dex_quote_token_address"),
                    payload.get("dex_quote_token_decimals"),
                    payload.get("dex_slippage"),
                    int(bool(payload.get("is_active", True))),
                    now,
                    now,
                ),
            )
            token_id = int(cursor.lastrowid)
            conn.commit()
        return self.get_token(token_id)

    def get_token(self, token_id: int) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tokens WHERE id = ?", (token_id,)).fetchone()
        return self._row_to_dict(row)

    def update_token(self, token_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "symbol",
            "market_type",
            "buy_drop_pct",
            "buy_drop_reference_price",
            "buy_price",
            "sell_profit_pct",
            "sell_price",
            "stop_loss_pct",
            "dex_chain_id",
            "dex_token_address",
            "dex_quote_token_address",
            "dex_quote_token_decimals",
            "dex_slippage",
            "is_active",
        }
        updates = {k: payload[k] for k in payload if k in allowed}
        if not updates:
            return self.get_token(token_id)
        if "symbol" in updates:
            updates["symbol"] = str(updates["symbol"]).strip().upper()
            if not updates["symbol"]:
                raise ValueError("symbol must be non-empty")
        if "market_type" in updates and updates["market_type"] is not None:
            updates["market_type"] = str(updates["market_type"]).strip().upper()
        for key in ("dex_chain_id", "dex_token_address", "dex_quote_token_address"):
            if key in updates and updates[key] is not None:
                updates[key] = str(updates[key]).strip()
        if "dex_quote_token_decimals" in updates:
            raw = updates["dex_quote_token_decimals"]
            updates["dex_quote_token_decimals"] = None if raw is None else int(raw)
        if "dex_slippage" in updates:
            raw = updates["dex_slippage"]
            updates["dex_slippage"] = None if raw is None else float(raw)
        if "is_active" in updates:
            updates["is_active"] = int(_to_bool(updates["is_active"]))
        updates["updated_at"] = utc_now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        params = list(updates.values())
        params.append(token_id)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE tokens SET {set_clause} WHERE id = ?", params)
            conn.commit()
        return self.get_token(token_id)

    def delete_token(self, token_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
            conn.commit()

    def append_trade(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (
                    symbol, action, price, quantity, notional_usd, pnl_pct, reason, mode, raw_order_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("symbol"),
                    payload.get("action"),
                    payload.get("price"),
                    payload.get("quantity"),
                    payload.get("notional_usd"),
                    payload.get("pnl_pct"),
                    payload.get("reason"),
                    payload.get("mode", "UNKNOWN"),
                    payload.get("raw_order_json"),
                    now,
                ),
            )
            trade_id = int(cursor.lastrowid)
            conn.commit()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return self._row_to_dict(row)

    def list_trades(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def append_log(self, level: str, message: str, context_json: str | None = None) -> dict[str, Any]:
        now = utc_now_iso()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO logs (level, message, context_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (level, message, context_json, now),
            )
            log_id = int(cursor.lastrowid)
            conn.commit()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM logs WHERE id = ?", (log_id,)).fetchone()
        return self._row_to_dict(row)

    def list_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_bot_state(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM bot_state WHERE id = 1").fetchone()
        return self._row_to_dict(row)

    def update_bot_state(self, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get_bot_state()
        fields["updated_at"] = utc_now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        params = list(fields.values())
        params.append(1)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE bot_state SET {set_clause} WHERE id = ?", params)
            conn.commit()
        return self.get_bot_state()

    def build_runtime_payload(self) -> dict[str, Any]:
        settings = self.get_settings()
        tokens = self.list_tokens()
        active_tokens = [token for token in tokens if int(token.get("is_active", 1)) == 1]
        market_type = str(settings.get("market_type", "CEX")).upper()
        if market_type == "DEX":
            active_tokens = [
                token
                for token in active_tokens
                if str(token.get("market_type") or "DEX").upper() == "DEX"
            ]
        else:
            active_tokens = [
                token
                for token in active_tokens
                if str(token.get("market_type") or "CEX").upper() == "CEX"
            ]
        return {
            "settings": settings,
            "tokens": active_tokens,
            "bot_state": self.get_bot_state(),
        }


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return float(value)


def _to_int(value: Any) -> int:
    return int(value)


def _normalize_settings_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if "market_type" in normalized and normalized["market_type"] is not None:
        normalized["market_type"] = str(normalized["market_type"]).strip().upper()
    for key in (
        "dex_base_url",
        "dex_project_id",
        "dex_chain_id",
        "dex_quote_token_address",
        "dex_wallet_address",
        "dex_private_key",
        "dex_rpc_url",
    ):
        if key in normalized and normalized[key] is not None:
            normalized[key] = str(normalized[key]).strip()

    bool_fields = {
        "telegram_enabled",
        "telegram_notify_positions",
        "dry_run",
        "okx_simulated",
        "dex_live_execute",
    }
    float_fields = {
        "trade_amount_usd",
        "request_timeout_seconds",
        "retry_backoff_seconds",
        "telegram_timeout_seconds",
        "telegram_retry_backoff_seconds",
        "dex_slippage",
        "dex_price_probe_quote_amount",
    }
    int_fields = {
        "max_active_trades",
        "poll_interval_seconds",
        "max_retries",
        "telegram_max_retries",
        "dex_quote_token_decimals",
    }

    for key in bool_fields:
        if key in normalized:
            normalized[key] = int(_to_bool(normalized[key]))
    for key in float_fields:
        if key in normalized:
            normalized[key] = _to_float_or_none(normalized[key])
    for key in int_fields:
        if key in normalized:
            normalized[key] = _to_int(normalized[key])
    return normalized


def _normalize_token_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if "symbol" in normalized and normalized["symbol"] is not None:
        normalized["symbol"] = str(normalized["symbol"]).strip().upper()
    if "market_type" in normalized and normalized["market_type"] is not None:
        normalized["market_type"] = str(normalized["market_type"]).strip().upper()
    for key in ("dex_chain_id", "dex_token_address", "dex_quote_token_address"):
        if key in normalized and normalized[key] is not None:
            normalized[key] = str(normalized[key]).strip()

    float_fields = {
        "buy_drop_pct",
        "buy_drop_reference_price",
        "buy_price",
        "sell_profit_pct",
        "sell_price",
        "stop_loss_pct",
        "dex_slippage",
    }
    for key in float_fields:
        if key in normalized:
            normalized[key] = _to_float_or_none(normalized[key])

    if "dex_quote_token_decimals" in normalized:
        raw = normalized["dex_quote_token_decimals"]
        normalized["dex_quote_token_decimals"] = None if raw is None else _to_int(raw)

    if "is_active" in normalized:
        normalized["is_active"] = int(_to_bool(normalized["is_active"]))
    return normalized


def create_control_api(
    *,
    store: ControlStore,
    start_bot_cb: Callable[[], dict[str, Any]],
    stop_bot_cb: Callable[[], dict[str, Any]],
    restart_bot_cb: Callable[[], dict[str, Any]],
    get_bot_runtime_status_cb: Callable[[], dict[str, Any]],
) -> Flask:
    app = Flask(__name__)
    CORS(app)
    sock = Sock(app)
    ws_clients: list[Any] = []
    ws_lock = threading.Lock()

    def publish_ws_event(event_type: str, data: dict[str, Any]) -> None:
        event = {"type": event_type, "data": data, "timestamp": utc_now_iso()}
        message = json.dumps(event, default=str)
        dead_clients: list[Any] = []
        with ws_lock:
            for ws in ws_clients:
                try:
                    ws.send(message)
                except Exception:
                    dead_clients.append(ws)
            for ws in dead_clients:
                try:
                    ws_clients.remove(ws)
                except ValueError:
                    pass

    @sock.route("/ws/events")
    def ws_events(ws: Any) -> None:
        with ws_lock:
            ws_clients.append(ws)
        try:
            ws.send(json.dumps({"type": "connected", "timestamp": utc_now_iso()}))
            while True:
                incoming = ws.receive()
                if incoming is None:
                    break
        except Exception:
            logger.debug("WebSocket client disconnected")
        finally:
            with ws_lock:
                if ws in ws_clients:
                    ws_clients.remove(ws)

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"ok": True})

    @app.get("/api/runtime/config")
    def runtime_config() -> Any:
        return jsonify(store.build_runtime_payload())

    @app.get("/api/bot/status")
    def bot_status() -> Any:
        db_state = store.get_bot_state()
        runtime_state = get_bot_runtime_status_cb()
        return jsonify({"db": db_state, "runtime": runtime_state})

    @app.post("/api/bot/start")
    def bot_start() -> Any:
        result = start_bot_cb()
        publish_ws_event("bot_status", result)
        return jsonify(result)

    @app.post("/api/bot/stop")
    def bot_stop() -> Any:
        result = stop_bot_cb()
        publish_ws_event("bot_status", result)
        return jsonify(result)

    @app.post("/api/bot/restart")
    def bot_restart() -> Any:
        result = restart_bot_cb()
        publish_ws_event("bot_status", result)
        return jsonify(result)

    @app.get("/api/settings")
    def get_settings() -> Any:
        return jsonify(store.get_settings())

    @app.put("/api/settings")
    def put_settings() -> Any:
        payload = request.get_json(silent=True) or {}
        normalized = _normalize_settings_payload(payload)
        updated = store.update_settings(normalized)
        publish_ws_event("settings_updated", updated)
        return jsonify(updated)

    @app.get("/api/tokens")
    def get_tokens() -> Any:
        return jsonify(store.list_tokens())

    @app.post("/api/tokens")
    def post_tokens() -> Any:
        payload = request.get_json(silent=True) or {}
        token = store.create_token(_normalize_token_payload(payload))
        publish_ws_event("token_created", token)
        return jsonify(token), 201

    @app.put("/api/tokens/<int:token_id>")
    def put_token(token_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        updated = store.update_token(token_id, _normalize_token_payload(payload))
        if not updated:
            return jsonify({"error": "token not found"}), 404
        publish_ws_event("token_updated", updated)
        return jsonify(updated)

    @app.delete("/api/tokens/<int:token_id>")
    def delete_token(token_id: int) -> Any:
        token = store.get_token(token_id)
        if not token:
            return jsonify({"error": "token not found"}), 404
        store.delete_token(token_id)
        publish_ws_event("token_deleted", {"id": token_id})
        return jsonify({"deleted": True, "id": token_id})

    @app.get("/api/trades")
    def get_trades() -> Any:
        limit = int(request.args.get("limit", 100))
        return jsonify(store.list_trades(limit=max(1, min(limit, 500))))

    @app.get("/api/logs")
    def get_logs() -> Any:
        limit = int(request.args.get("limit", 200))
        return jsonify(store.list_logs(limit=max(1, min(limit, 1000))))

    @app.post("/api/events/trade")
    def event_trade() -> Any:
        payload = request.get_json(silent=True) or {}
        trade = store.append_trade(payload)
        store.append_log("INFO", f"TRADE {payload.get('action')} {payload.get('symbol')}")
        publish_ws_event("trade_update", trade)
        return jsonify(trade), 201

    @app.post("/api/events/error")
    def event_error() -> Any:
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", "worker error"))
        log = store.append_log("ERROR", message, context_json=json.dumps(payload, default=str))
        publish_ws_event("error", log)
        return jsonify(log), 201

    @app.post("/api/events/heartbeat")
    def event_heartbeat() -> Any:
        payload = request.get_json(silent=True) or {}
        updated = store.update_bot_state(
            last_execution_at=payload.get("last_execution_at") or utc_now_iso(),
            status=payload.get("status", "RUNNING"),
            pid=payload.get("pid"),
        )
        publish_ws_event("heartbeat", updated)
        return jsonify({"ok": True})

    @app.post("/api/telegram/test")
    def telegram_test() -> Any:
        payload = request.get_json(silent=True) or {}
        settings = store.get_settings()
        token = payload.get("telegram_bot_token") or settings.get("telegram_bot_token")
        chat_id = payload.get("telegram_chat_id") or settings.get("telegram_chat_id")
        notifier = build_notifier(
            enabled=True,
            bot_token=str(token) if token else None,
            chat_id=str(chat_id) if chat_id else None,
            timeout_seconds=float(settings.get("telegram_timeout_seconds", 10.0)),
            max_retries=int(settings.get("telegram_max_retries", 3)),
            retry_backoff_seconds=float(settings.get("telegram_retry_backoff_seconds", 1.5)),
        )
        if not notifier.enabled:
            return jsonify({"ok": False, "error": "telegram bot token/chat id missing"}), 400
        test_message = payload.get("message") or "Test notification from OKX bot backend"
        try:
            notifier.send(str(test_message))
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True})

    @app.post("/api/ws/publish")
    def ws_publish() -> Any:
        payload = request.get_json(silent=True) or {}
        event_type = str(payload.get("type", "custom"))
        data = payload.get("data", payload)
        publish_ws_event(event_type, data if isinstance(data, dict) else {"value": data})
        return jsonify({"ok": True})

    @app.get("/api/ui/overview")
    def ui_overview() -> Any:
        bot_state = store.get_bot_state()
        runtime = get_bot_runtime_status_cb()
        settings = store.get_settings()
        active_tokens = [t for t in store.list_tokens() if int(t.get("is_active", 1)) == 1]
        recent_trades = store.list_trades(limit=20)
        recent_logs = store.list_logs(limit=20)
        payload = {
            "bot_status": bot_state.get("status", "STOPPED"),
            "runtime": runtime,
            "active_trades_count": len(
                [trade for trade in recent_trades if str(trade.get("action")) == "BUY"]
            ),
            "max_active_trades": settings.get("max_active_trades"),
            "last_execution_at": bot_state.get("last_execution_at"),
            "tokens_active_count": len(active_tokens),
            "recent_trades": recent_trades,
            "recent_logs": recent_logs,
        }
        return jsonify(payload)

    return app


def post_json(url: str, payload: dict[str, Any], timeout: float = 5.0) -> None:
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
    except Exception:
        logger.exception("Failed to POST event to %s", url)
