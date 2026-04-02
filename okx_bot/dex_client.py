"""OKX DEX API client helpers for quote and swap building."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time
from typing import Any

import requests


class OkxDexError(RuntimeError):
    pass


@dataclass(frozen=True)
class OkxDexCredentials:
    api_key: str
    secret_key: str
    passphrase: str
    project_id: str
    base_url: str = "https://web3.okx.com"


@dataclass(frozen=True)
class DexTokenMetadata:
    address: str
    decimals: int
    symbol: str | None = None


class OkxDexClient:
    """Client for OKX DEX aggregator quote/swap endpoints."""

    def __init__(
        self,
        credentials: OkxDexCredentials,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.5,
    ) -> None:
        self.credentials = credentials
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.session = requests.Session()

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        message = f"{timestamp}{method}{request_path}{body}"
        digest = hmac.new(
            self.credentials.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _headers(self, timestamp: str, method: str, request_path: str, body: str = "") -> dict[str, str]:
        return {
            "OK-ACCESS-KEY": self.credentials.api_key,
            "OK-ACCESS-SIGN": self._sign(timestamp, method, request_path, body),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
            "OK-ACCESS-PROJECT": self.credentials.project_id,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        request_path = path
        if params:
            query = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
            request_path = f"{path}?{query}"
        url = f"{self.credentials.base_url}{request_path}"
        body = ""

        for attempt in range(1, self.max_retries + 1):
            timestamp = self._timestamp()
            headers = self._headers(timestamp, method, request_path, body)
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                if str(data.get("code")) != "0":
                    raise OkxDexError(f"OKX DEX error code={data.get('code')} msg={data.get('msg')}")
                return data
            except (requests.RequestException, ValueError, OkxDexError) as exc:
                if attempt >= self.max_retries:
                    raise OkxDexError(f"DEX request failed after {attempt} attempts: {exc}") from exc
                time.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))
        raise OkxDexError("Unexpected retry loop exit")

    @staticmethod
    def amount_to_units(amount: float, decimals: int) -> int:
        return int(round(amount * (10 ** decimals)))

    @staticmethod
    def units_to_amount(units: str | int | float, decimals: int) -> float:
        return float(units) / (10 ** decimals)

    def get_quote(
        self,
        *,
        chain_id: str,
        from_token_address: str,
        to_token_address: str,
        amount_units: int,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v5/dex/aggregator/quote",
            params={
                "chainId": str(chain_id),
                "fromTokenAddress": from_token_address,
                "toTokenAddress": to_token_address,
                "amount": str(amount_units),
            },
        )

    def get_swap_payload(
        self,
        *,
        chain_id: str,
        from_token_address: str,
        to_token_address: str,
        amount_units: int,
        slippage: float,
        user_wallet_address: str,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v5/dex/aggregator/swap",
            params={
                "chainId": str(chain_id),
                "fromTokenAddress": from_token_address,
                "toTokenAddress": to_token_address,
                "amount": str(amount_units),
                "slippage": str(slippage),
                "userWalletAddress": user_wallet_address,
            },
        )

