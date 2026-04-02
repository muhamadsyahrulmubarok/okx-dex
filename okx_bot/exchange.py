"""Exchange adapters for CEX and DEX trading modes."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Protocol

from web3 import HTTPProvider, Web3
from web3.exceptions import TransactionNotFound

from okx_bot.client import OkxClient
from okx_bot.config import BotConfig, TokenRule
from okx_bot.dex_client import OkxDexClient, OkxDexError

logger = logging.getLogger(__name__)

ETH_NATIVE = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


class ExchangeAdapter(Protocol):
    def get_price(self, token: TokenRule) -> float:
        ...

    def buy(self, token: TokenRule, amount_usd: float, observed_price: float) -> tuple[float, dict[str, Any]]:
        ...

    def sell(self, token: TokenRule, quantity: float, observed_price: float) -> dict[str, Any]:
        ...

    @property
    def mode(self) -> str:
        ...


class CexExchangeAdapter:
    def __init__(self, client: OkxClient, *, dry_run: bool = False) -> None:
        self.client = client
        self.dry_run = dry_run

    @property
    def mode(self) -> str:
        return "CEX"

    @staticmethod
    def _estimate_qty(amount_usd: float, price: float) -> float:
        if price <= 0:
            raise ValueError("price must be > 0")
        return amount_usd / price

    def get_price(self, token: TokenRule) -> float:
        return self.client.get_last_price(token.symbol)

    def buy(self, token: TokenRule, amount_usd: float, observed_price: float) -> tuple[float, dict[str, Any]]:
        qty = self._estimate_qty(amount_usd, observed_price)
        if self.dry_run:
            return qty, {
                "dry_run": True,
                "mode": "CEX",
                "action": "BUY",
                "symbol": token.symbol,
                "notional_usd": amount_usd,
                "estimated_quantity": qty,
            }
        result = self.client.place_market_order(token.symbol, "buy", amount_usd, tgt_ccy="quote_ccy")
        try:
            data = result.get("data") or []
            if data and data[0].get("fillSz"):
                qty = float(data[0]["fillSz"])
        except (TypeError, ValueError):
            logger.debug("Unable to parse fillSz for CEX buy %s", token.symbol)
        return qty, result

    def sell(self, token: TokenRule, quantity: float, observed_price: float) -> dict[str, Any]:
        _ = observed_price
        if self.dry_run:
            return {
                "dry_run": True,
                "mode": "CEX",
                "action": "SELL",
                "symbol": token.symbol,
                "quantity": quantity,
            }
        return self.client.place_market_order(token.symbol, "sell", quantity)


@dataclass(frozen=True)
class DexRuntimeSettings:
    chain_id: str
    quote_token_address: str
    quote_token_decimals: int
    slippage: float
    wallet_address: str
    private_key: str | None
    rpc_url: str | None
    live_execute: bool
    price_probe_quote_amount: float


class DexExchangeAdapter:
    """DEX adapter using OKX quote/swap API + optional onchain submit."""

    def __init__(
        self,
        dex_client: OkxDexClient,
        runtime: DexRuntimeSettings,
        *,
        dry_run: bool = True,
    ) -> None:
        self.dex_client = dex_client
        self.runtime = runtime
        self.dry_run = dry_run

    @property
    def mode(self) -> str:
        return "DEX"

    def _token_chain_id(self, token: TokenRule) -> str:
        return token.dex_chain_id or self.runtime.chain_id

    def _token_address(self, token: TokenRule) -> str:
        if token.dex_token_address:
            return token.dex_token_address
        # allow user to place address into symbol for DEX mode for convenience
        if token.symbol.lower().startswith("0x"):
            return token.symbol
        raise ValueError(
            f"DEX token {token.symbol} missing dex_token_address (or symbol as address)"
        )

    def _quote_token_address(self, token: TokenRule) -> str:
        return token.dex_quote_token_address or self.runtime.quote_token_address

    def _quote_decimals(self, token: TokenRule) -> int:
        return token.dex_quote_token_decimals or self.runtime.quote_token_decimals

    def _slippage(self, token: TokenRule) -> float:
        return token.dex_slippage if token.dex_slippage is not None else self.runtime.slippage

    def _quote_to_token(self, token: TokenRule, quote_amount: float) -> dict[str, Any]:
        amount_units = self.dex_client.amount_to_units(quote_amount, self._quote_decimals(token))
        return self.dex_client.get_quote(
            chain_id=self._token_chain_id(token),
            from_token_address=self._quote_token_address(token),
            to_token_address=self._token_address(token),
            amount_units=amount_units,
        )

    def _quote_token_to_quote(self, token: TokenRule, token_units: int) -> dict[str, Any]:
        return self.dex_client.get_quote(
            chain_id=self._token_chain_id(token),
            from_token_address=self._token_address(token),
            to_token_address=self._quote_token_address(token),
            amount_units=token_units,
        )

    def get_price(self, token: TokenRule) -> float:
        probe = max(self.runtime.price_probe_quote_amount, 1e-9)
        quote = self._quote_to_token(token, probe)
        rows = quote.get("data") or []
        if not rows:
            raise OkxDexError(f"No DEX quote data for {token.symbol}")
        row = rows[0]
        to_units = float(row.get("toTokenAmount") or 0)
        if to_units <= 0:
            raise OkxDexError(f"DEX quote for {token.symbol} returned zero output")
        token_amount = self.dex_client.units_to_amount(to_units, int(row["toToken"]["decimal"]))
        if token_amount <= 0:
            raise OkxDexError(f"DEX quote for {token.symbol} returned invalid token amount")
        return probe / token_amount

    def buy(self, token: TokenRule, amount_usd: float, observed_price: float) -> tuple[float, dict[str, Any]]:
        quote = self._quote_to_token(token, amount_usd)
        rows = quote.get("data") or []
        if not rows:
            raise OkxDexError(f"No DEX quote data for BUY {token.symbol}")
        row = rows[0]
        token_decimals = int(row["toToken"]["decimal"])
        token_out_units = int(float(row["toTokenAmount"]))
        qty = self.dex_client.units_to_amount(token_out_units, token_decimals)

        swap_payload = self.dex_client.get_swap_payload(
            chain_id=self._token_chain_id(token),
            from_token_address=self._quote_token_address(token),
            to_token_address=self._token_address(token),
            amount_units=self.dex_client.amount_to_units(amount_usd, self._quote_decimals(token)),
            slippage=self._slippage(token),
            user_wallet_address=self.runtime.wallet_address,
        )

        if self.dry_run or not self.runtime.live_execute:
            return qty, {
                "dry_run": True,
                "mode": "DEX",
                "action": "BUY",
                "symbol": token.symbol,
                "notional_usd": amount_usd,
                "observed_price": observed_price,
                "expected_quantity": qty,
                "swap_payload": swap_payload,
            }

        tx_result = self._submit_onchain_swap(swap_payload)
        return qty, {
            "mode": "DEX",
            "action": "BUY",
            "symbol": token.symbol,
            "notional_usd": amount_usd,
            "expected_quantity": qty,
            "swap_payload": swap_payload,
            "tx_result": tx_result,
        }

    def sell(self, token: TokenRule, quantity: float, observed_price: float) -> dict[str, Any]:
        token_decimals = 18
        try:
            # use reverse quote to discover decimals/output
            preview_units = max(int(quantity * (10 ** token_decimals)), 1)
            preview_quote = self._quote_token_to_quote(token, preview_units)
            rows = preview_quote.get("data") or []
            if rows:
                token_decimals = int(rows[0]["fromToken"]["decimal"])
        except Exception:
            logger.debug("Unable to discover DEX decimals via preview for %s", token.symbol)
        token_units = max(int(quantity * (10 ** token_decimals)), 1)

        swap_payload = self.dex_client.get_swap_payload(
            chain_id=self._token_chain_id(token),
            from_token_address=self._token_address(token),
            to_token_address=self._quote_token_address(token),
            amount_units=token_units,
            slippage=self._slippage(token),
            user_wallet_address=self.runtime.wallet_address,
        )
        if self.dry_run or not self.runtime.live_execute:
            return {
                "dry_run": True,
                "mode": "DEX",
                "action": "SELL",
                "symbol": token.symbol,
                "quantity": quantity,
                "observed_price": observed_price,
                "swap_payload": swap_payload,
            }
        tx_result = self._submit_onchain_swap(swap_payload)
        return {
            "mode": "DEX",
            "action": "SELL",
            "symbol": token.symbol,
            "quantity": quantity,
            "swap_payload": swap_payload,
            "tx_result": tx_result,
        }

    def _submit_onchain_swap(self, swap_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.runtime.private_key:
            raise OkxDexError("DEX live execute requires dex_private_key")
        if not self.runtime.rpc_url:
            raise OkxDexError("DEX live execute requires dex_rpc_url")
        rows = swap_payload.get("data") or []
        if not rows:
            raise OkxDexError("No swap payload data returned by DEX API")
        tx_obj = rows[0].get("tx") or {}
        if not tx_obj:
            raise OkxDexError("Missing tx object in DEX swap payload")

        w3 = Web3(HTTPProvider(self.runtime.rpc_url))
        if not w3.is_connected():
            raise OkxDexError("Unable to connect to chain RPC for DEX execution")

        account = w3.eth.account.from_key(self.runtime.private_key)
        if account.address.lower() != self.runtime.wallet_address.lower():
            raise OkxDexError("dex_wallet_address does not match dex_private_key")

        chain_id = int(rows[0].get("routerResult", {}).get("chainId") or self.runtime.chain_id)
        nonce = w3.eth.get_transaction_count(account.address)
        tx: dict[str, Any] = {
            "to": Web3.to_checksum_address(tx_obj["to"]),
            "from": account.address,
            "data": tx_obj["data"],
            "value": int(tx_obj.get("value") or 0),
            "nonce": nonce,
            "chainId": chain_id,
        }
        if tx_obj.get("gas"):
            tx["gas"] = int(tx_obj["gas"])
        if tx_obj.get("gasPrice"):
            tx["gasPrice"] = int(tx_obj["gasPrice"])
        if tx_obj.get("maxPriorityFeePerGas"):
            tx["maxPriorityFeePerGas"] = int(tx_obj["maxPriorityFeePerGas"])
            try:
                base_fee = w3.eth.get_block("latest").get("baseFeePerGas")
                if base_fee:
                    tx["maxFeePerGas"] = int(base_fee) + int(tx["maxPriorityFeePerGas"]) * 2
            except Exception:
                logger.debug("Unable to compute EIP-1559 maxFeePerGas; using gasPrice if present")

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
        result: dict[str, Any] = {"tx_hash": tx_hash}
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            result["status"] = int(receipt.status)
            result["block_number"] = int(receipt.blockNumber)
        except TransactionNotFound:
            result["status"] = "PENDING"
        except Exception:
            logger.exception("Failed waiting transaction receipt for %s", tx_hash)
            result["status"] = "SUBMITTED"
        return result
