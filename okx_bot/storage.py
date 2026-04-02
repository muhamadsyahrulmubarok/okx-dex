"""SQLite persistence for open positions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from okx_bot.domain import Position


class PositionStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    entry_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    entry_value_usd REAL NOT NULL,
                    opened_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def list_positions(self) -> dict[str, Position]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, entry_price, quantity, entry_value_usd, opened_at FROM positions"
            ).fetchall()
        return {
            row["symbol"]: Position(
                symbol=row["symbol"],
                entry_price=float(row["entry_price"]),
                quantity=float(row["quantity"]),
                entry_value_usd=float(row["entry_value_usd"]),
                opened_at=str(row["opened_at"]),
            )
            for row in rows
        }

    def upsert_position(self, position: Position) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO positions (symbol, entry_price, quantity, entry_value_usd, opened_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    entry_price=excluded.entry_price,
                    quantity=excluded.quantity,
                    entry_value_usd=excluded.entry_value_usd,
                    opened_at=excluded.opened_at
                """,
                (
                    position.symbol,
                    position.entry_price,
                    position.quantity,
                    position.entry_value_usd,
                    position.opened_at,
                ),
            )
            conn.commit()

    def delete_position(self, symbol: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
            conn.commit()
