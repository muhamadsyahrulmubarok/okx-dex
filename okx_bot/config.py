"""Configuration loading and validation for the rule-based OKX bot."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TokenRule:
    symbol: str
    market_type: str | None = None
    buy_drop_pct: float | None = None
    buy_drop_reference_price: float | None = None
    buy_price: float | None = None
    sell_profit_pct: float | None = None
    sell_price: float | None = None
    stop_loss_pct: float | None = None
    dex_chain_id: str | None = None
    dex_token_address: str | None = None
    dex_quote_token_address: str | None = None
    dex_quote_token_decimals: int | None = None
    dex_slippage: float | None = None


@dataclass(frozen=True)
class BotConfig:
    market_type: str
    max_active_trades: int
    trade_amount_usd: float
    poll_interval_seconds: int
    request_timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    dex_base_url: str
    dex_project_id: str | None
    dex_chain_id: str | None
    dex_quote_token_address: str | None
    dex_quote_token_decimals: int
    dex_slippage: float
    dex_wallet_address: str | None
    dex_private_key: str | None
    dex_rpc_url: str | None
    dex_live_execute: bool
    dex_price_probe_quote_amount: float
    tokens: list[TokenRule]


def _to_int(value: Any, field_name: str, allow_none: bool = True) -> int | None:
    if value is None and allow_none:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer value for '{field_name}': {value!r}") from exc


def _to_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return False
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError(f"Invalid boolean value for '{field_name}': {value!r}")


def _normalize_market_type(value: Any, field_name: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    normalized = str(value or "").strip().upper()
    if normalized not in {"CEX", "DEX"}:
        raise ValueError(f"Invalid '{field_name}'. Expected 'CEX' or 'DEX', got {value!r}")
    return normalized


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
        market_type=_normalize_market_type(raw.get("market_type"), "market_type", allow_none=True),
        buy_drop_pct=_to_float(raw.get("buy_drop_pct"), "buy_drop_pct"),
        buy_drop_reference_price=_to_float(
            raw.get("buy_drop_reference_price"), "buy_drop_reference_price"
        ),
        buy_price=_to_float(raw.get("buy_price"), "buy_price"),
        sell_profit_pct=_to_float(raw.get("sell_profit_pct"), "sell_profit_pct"),
        sell_price=_to_float(raw.get("sell_price"), "sell_price"),
        stop_loss_pct=_to_float(raw.get("stop_loss_pct"), "stop_loss_pct"),
        dex_chain_id=str(raw["dex_chain_id"]).strip() if raw.get("dex_chain_id") is not None else None,
        dex_token_address=(
            str(raw["dex_token_address"]).strip() if raw.get("dex_token_address") is not None else None
        ),
        dex_quote_token_address=(
            str(raw["dex_quote_token_address"]).strip()
            if raw.get("dex_quote_token_address") is not None
            else None
        ),
        dex_quote_token_decimals=_to_int(
            raw.get("dex_quote_token_decimals"), "dex_quote_token_decimals"
        ),
        dex_slippage=_to_float(raw.get("dex_slippage"), "dex_slippage"),
    )


def _parse_config_dict(raw: dict[str, Any]) -> BotConfig:
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

    market_type = _normalize_market_type(raw.get("market_type", "CEX"), "market_type")

    dex_quote_token_decimals = _to_int(
        raw.get("dex_quote_token_decimals", 6), "dex_quote_token_decimals", allow_none=False
    )
    if dex_quote_token_decimals is None or dex_quote_token_decimals < 0:
        raise ValueError("'dex_quote_token_decimals' must be >= 0")

    dex_slippage = float(raw.get("dex_slippage", 0.005))
    if dex_slippage < 0 or dex_slippage > 1:
        raise ValueError("'dex_slippage' must be between 0 and 1")

    dex_live_execute = _to_bool(raw.get("dex_live_execute", False), "dex_live_execute")

    dex_price_probe_quote_amount = float(raw.get("dex_price_probe_quote_amount", 1.0))
    if dex_price_probe_quote_amount <= 0:
        raise ValueError("'dex_price_probe_quote_amount' must be > 0")

    return BotConfig(
        market_type=market_type,
        max_active_trades=max_active_trades,
        trade_amount_usd=trade_amount_usd,
        poll_interval_seconds=poll_interval_seconds,
        request_timeout_seconds=request_timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        dex_base_url=str(raw.get("dex_base_url", "https://web3.okx.com")),
        dex_project_id=(
            str(raw["dex_project_id"]).strip() if raw.get("dex_project_id") is not None else None
        ),
        dex_chain_id=str(raw["dex_chain_id"]).strip() if raw.get("dex_chain_id") is not None else None,
        dex_quote_token_address=(
            str(raw["dex_quote_token_address"]).strip()
            if raw.get("dex_quote_token_address") is not None
            else None
        ),
        dex_quote_token_decimals=dex_quote_token_decimals,
        dex_slippage=dex_slippage,
        dex_wallet_address=(
            str(raw["dex_wallet_address"]).strip() if raw.get("dex_wallet_address") is not None else None
        ),
        dex_private_key=(
            str(raw["dex_private_key"]).strip() if raw.get("dex_private_key") is not None else None
        ),
        dex_rpc_url=str(raw["dex_rpc_url"]).strip() if raw.get("dex_rpc_url") is not None else None,
        dex_live_execute=dex_live_execute,
        dex_price_probe_quote_amount=dex_price_probe_quote_amount,
        tokens=tokens,
    )


def load_config_dict(raw: dict[str, Any]) -> BotConfig:
    return _parse_config_dict(raw)


def load_config(path: str | Path) -> BotConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return _parse_config_dict(raw)
