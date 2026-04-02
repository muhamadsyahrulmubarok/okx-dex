"""Telegram Bot API notifier."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


class TelegramNotifyError(RuntimeError):
    pass


class Notifier:
    @property
    def enabled(self) -> bool:
        return False

    def send(self, message: str) -> None:
        return None


class NullNotifier(Notifier):
    pass


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    timeout_seconds: float = 10.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.5
    disable_web_page_preview: bool = True


class TelegramNotifier(Notifier):
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return True

    def _truncate(self, message: str) -> str:
        # Telegram message hard limit is 4096 chars.
        limit = 4096
        if len(message) <= limit:
            return message
        return f"{message[: limit - 28]} ...[truncated by bot]"

    def send(self, message: str) -> None:
        payload: dict[str, Any] = {
            "chat_id": self.config.chat_id,
            "text": self._truncate(message),
            "disable_web_page_preview": self.config.disable_web_page_preview,
        }
        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.session.post(
                    url=url,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                if not bool(data.get("ok")):
                    raise TelegramNotifyError(f"Telegram API returned non-ok response: {data}")
                return
            except (requests.RequestException, ValueError, TelegramNotifyError) as exc:
                if attempt >= self.config.max_retries:
                    raise TelegramNotifyError(
                        f"Telegram notification failed after {attempt} attempts: {exc}"
                    ) from exc
                sleep_seconds = self.config.retry_backoff_seconds * (2 ** (attempt - 1))
                time.sleep(sleep_seconds)


def build_notifier(
    *,
    enabled: bool,
    bot_token: str | None,
    chat_id: str | None,
    timeout_seconds: float = 10.0,
    max_retries: int = 3,
    retry_backoff_seconds: float = 1.5,
) -> Notifier:
    if not enabled:
        return NullNotifier()
    if not bot_token or not chat_id:
        logger.warning(
            "Telegram enabled but missing TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID; notifications disabled."
        )
        return NullNotifier()
    return TelegramNotifier(
        TelegramConfig(
            bot_token=bot_token,
            chat_id=chat_id,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )
    )
