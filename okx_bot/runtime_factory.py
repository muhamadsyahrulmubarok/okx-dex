"""Factory helpers to build runtime config, notifier, and exchange adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from okx_bot.client import OkxClient, OkxCredentials
from okx_bot.config import BotConfig, load_config_dict
from okx_bot.dex_client import OkxDexClient, OkxDexCredentials
from okx_bot.exchange import CexExchangeAdapter, DexExchangeAdapter, DexRuntimeSettings, ExchangeAdapter
from okx_bot.telegram import Notifier, build_notifier


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeBundle:
    config: BotConfig
    exchange: ExchangeAdapter
    dry_run: bool
    notifier: Notifier


def build_runtime_bundle(
    *,
    settings: dict[str, Any],
    tokens: list[dict[str, Any]],
    okx_api_key: str,
    okx_secret_key: str,
    okx_passphrase: str,
    okx_base_url: str,
) -> RuntimeBundle:
    bot_config_payload = {
        "market_type": settings.get("market_type", "CEX"),
        "max_active_trades": settings.get("max_active_trades", 6),
        "trade_amount_usd": settings.get("trade_amount_usd", 100),
        "poll_interval_seconds": settings.get("poll_interval_seconds", 10),
        "request_timeout_seconds": settings.get("request_timeout_seconds", 10),
        "max_retries": settings.get("max_retries", 3),
        "retry_backoff_seconds": settings.get("retry_backoff_seconds", 1.5),
        "dex_base_url": settings.get("dex_base_url", "https://web3.okx.com"),
        "dex_project_id": settings.get("dex_project_id"),
        "dex_chain_id": settings.get("dex_chain_id"),
        "dex_quote_token_address": settings.get("dex_quote_token_address"),
        "dex_quote_token_decimals": settings.get("dex_quote_token_decimals", 6),
        "dex_slippage": settings.get("dex_slippage", 0.005),
        "dex_wallet_address": settings.get("dex_wallet_address"),
        "dex_private_key": settings.get("dex_private_key"),
        "dex_rpc_url": settings.get("dex_rpc_url"),
        "dex_live_execute": settings.get("dex_live_execute", False),
        "dex_price_probe_quote_amount": settings.get("dex_price_probe_quote_amount", 1.0),
        "tokens": tokens,
    }
    config = load_config_dict(bot_config_payload)
    dry_run = _to_bool(settings.get("dry_run", True))

    notifier = build_notifier(
        enabled=_to_bool(settings.get("telegram_enabled", False)),
        bot_token=settings.get("telegram_bot_token"),
        chat_id=settings.get("telegram_chat_id"),
        timeout_seconds=float(settings.get("telegram_timeout_seconds", 10.0)),
        max_retries=int(settings.get("telegram_max_retries", 3)),
        retry_backoff_seconds=float(settings.get("telegram_retry_backoff_seconds", 1.5)),
    )

    if config.market_type == "DEX":
        if not config.dex_project_id:
            raise ValueError(
                "DEX mode requires dex_project_id (OKX web3 project id) in settings"
            )
        dex_credentials = OkxDexCredentials(
            api_key=okx_api_key,
            secret_key=okx_secret_key,
            passphrase=okx_passphrase,
            project_id=config.dex_project_id,
            base_url=config.dex_base_url,
        )
        dex_client = OkxDexClient(
            dex_credentials,
            timeout_seconds=config.request_timeout_seconds,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
        )
        if not config.dex_chain_id:
            raise ValueError("DEX mode requires dex_chain_id in settings")
        if not config.dex_quote_token_address:
            raise ValueError("DEX mode requires dex_quote_token_address in settings")
        if not config.dex_wallet_address:
            raise ValueError("DEX mode requires dex_wallet_address in settings")

        runtime = DexRuntimeSettings(
            chain_id=config.dex_chain_id,
            quote_token_address=config.dex_quote_token_address,
            quote_token_decimals=config.dex_quote_token_decimals,
            slippage=config.dex_slippage,
            wallet_address=config.dex_wallet_address,
            private_key=config.dex_private_key,
            rpc_url=config.dex_rpc_url,
            live_execute=config.dex_live_execute,
            price_probe_quote_amount=config.dex_price_probe_quote_amount,
        )
        exchange: ExchangeAdapter = DexExchangeAdapter(
            dex_client,
            runtime,
            dry_run=dry_run,
        )
    else:
        simulated = _to_bool(settings.get("okx_simulated", False))
        credentials = OkxCredentials(
            api_key=okx_api_key,
            secret_key=okx_secret_key,
            passphrase=okx_passphrase,
            base_url=okx_base_url,
            simulated=simulated,
        )
        cex_client = OkxClient(
            credentials=credentials,
            timeout_seconds=config.request_timeout_seconds,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
        )
        exchange = CexExchangeAdapter(cex_client, dry_run=dry_run)

    return RuntimeBundle(config=config, exchange=exchange, dry_run=dry_run, notifier=notifier)

