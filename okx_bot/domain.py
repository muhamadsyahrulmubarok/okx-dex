"""Core domain models for trading bot state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Position:
    symbol: str
    entry_price: float
    quantity: float
    entry_value_usd: float
    opened_at: str = field(default_factory=utc_now_iso)

    def pnl_pct(self, current_price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return ((current_price - self.entry_price) / self.entry_price) * 100


@dataclass(frozen=True)
class TradeFill:
    symbol: str
    side: str
    price: float
    quantity: float
    notional_usd: float
    raw_response: dict[str, Any]

