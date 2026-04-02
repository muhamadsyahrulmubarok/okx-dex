"""HTTP client for OKX API v5 with request signing and retries."""

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


@dataclass(frozen=True)
class OkxCredentials:
    api_key: str
    secret_key: str
    passphrase: str
    base_url: str
    simulated: bool = False


class OkxApiError(RuntimeError):
    pass


class OkxClient:
    def __init__(
        self,
        credentials: OkxCredentials,
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

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        message = f"{timestamp}{method}{path}{body}"
        digest = hmac.new(
            self.credentials.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        timestamp = self._timestamp()
        headers = {
            "OK-ACCESS-KEY": self.credentials.api_key,
            "OK-ACCESS-SIGN": self._sign(timestamp, method, path, body),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
            "Content-Type": "application/json",
        }
        if self.credentials.simulated:
            headers["x-simulated-trading"] = "1"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body_obj: dict[str, Any] | None = None,
        requires_auth: bool = False,
    ) -> dict[str, Any]:
        url = f"{self.credentials.base_url}{path}"
        body = json.dumps(body_obj) if body_obj is not None else ""

        for attempt in range(1, self.max_retries + 1):
            headers = self._headers(method, path, body) if requires_auth else {}
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=body if body else None,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                code = data.get("code")
                if code is not None and str(code) != "0":
                    raise OkxApiError(f"OKX API error code={code} message={data.get('msg')}")
                return data
            except (requests.RequestException, ValueError, OkxApiError) as exc:
                if attempt >= self.max_retries:
                    raise OkxApiError(f"Request failed after {attempt} attempts: {exc}") from exc
                sleep_seconds = self.retry_backoff_seconds * (2 ** (attempt - 1))
                time.sleep(sleep_seconds)
        raise OkxApiError("Unexpected request retry loop exit")

    def get_last_price(self, symbol: str) -> float:
        data = self._request(
            "GET",
            "/api/v5/market/ticker",
            params={"instId": symbol},
            requires_auth=False,
        )
        rows = data.get("data") or []
        if not rows:
            raise OkxApiError(f"No ticker data returned for symbol {symbol}")
        return float(rows[0]["last"])

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        *,
        tgt_ccy: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "instId": symbol,
            "tdMode": "cash",
            "side": side,
            "ordType": "market",
            "sz": f"{quantity:.12f}".rstrip("0").rstrip("."),
        }
        if tgt_ccy:
            body["tgtCcy"] = tgt_ccy
        return self._request("POST", "/api/v5/trade/order", body_obj=body, requires_auth=True)
