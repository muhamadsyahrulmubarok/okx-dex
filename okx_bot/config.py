"""Configuration loading and validation for the rule-based OKX bot."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TokenRule:
    symbol: str
    buy_drop_pct: float | None = None
    buy_drop_reference_price: float | None = None
    buy_price: float | None = None
    sell_profit_pct: float | None = None
    sell_price: float | None = None
    stop_loss_pct: float | None = None


@dataclass(frozen=True)
class BotConfig:
    max_active_trades: int
    trade_amount_usd: float
    poll_interval_seconds: int
    request_timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    tokens: list[TokenRule]


def _to_float(value: Any, field_name: str, allow_none: bool = True) -> float | None:
    if value is None and allow_none:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value for '{field_name}': {value!r}") from exc


def _parse_token(raw: dict[str, Any]) -> TokenRule:
    if "symbol" not in raw or not str(raw["symbol"]).strip():
        raise ValueError("Every token rule must include a non-empty 'symbol'")

    return TokenRule(
        symbol=str(raw["symbol"]).strip(),
        buy_drop_pct=_to_float(raw.get("buy_drop_pct"), "buy_drop_pct"),
        buy_drop_reference_price=_to_float(
            raw.get("buy_drop_reference_price"), "buy_drop_reference_price"
        ),
        buy_price=_to_float(raw.get("buy_price"), "buy_price"),
        sell_profit_pct=_to_float(raw.get("sell_profit_pct"), "sell_profit_pct"),
        sell_price=_to_float(raw.get("sell_price"), "sell_price"),
        stop_loss_pct=_to_float(raw.get("stop_loss_pct"), "stop_loss_pct"),
    )


def load_config(path: str | Path) -> BotConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    tokens_raw = raw.get("tokens")
    if not isinstance(tokens_raw, list) or not tokens_raw:
        raise ValueError("'tokens' must be a non-empty list")

    tokens = [_parse_token(token) for token in tokens_raw]

    max_active_trades = int(raw.get("max_active_trades", 6))
    if max_active_trades < 1:
        raise ValueError("'max_active_trades' must be >= 1")

    trade_amount_usd = _to_float(raw.get("trade_amount_usd", 100), "trade_amount_usd", allow_none=False)
    if trade_amount_usd is None or trade_amount_usd <= 0:
        raise ValueError("'trade_amount_usd' must be > 0")

    poll_interval_seconds = int(raw.get("poll_interval_seconds", 10))
    if poll_interval_seconds < 1:
        raise ValueError("'poll_interval_seconds' must be >= 1")

    request_timeout_seconds = float(raw.get("request_timeout_seconds", 10))
    if request_timeout_seconds <= 0:
        raise ValueError("'request_timeout_seconds' must be > 0")

    max_retries = int(raw.get("max_retries", 3))
    if max_retries < 1:
        raise ValueError("'max_retries' must be >= 1")

    retry_backoff_seconds = float(raw.get("retry_backoff_seconds", 1.5))
    if retry_backoff_seconds <= 0:
        raise ValueError("'retry_backoff_seconds' must be > 0")

    return BotConfig(
        max_active_trades=max_active_trades,
        trade_amount_usd=trade_amount_usd,
        poll_interval_seconds=poll_interval_seconds,
        request_timeout_seconds=request_timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        tokens=tokens,
    )
