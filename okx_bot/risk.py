"""Risk controls for position sizing and concurrency limits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str | None = None


class RiskController:
    def __init__(self, max_active_trades: int, trade_amount_usd: float) -> None:
        self.max_active_trades = max_active_trades
        self.trade_amount_usd = trade_amount_usd

    def can_open_new_trade(self, open_positions_count: int) -> RiskDecision:
        if open_positions_count >= self.max_active_trades:
            return RiskDecision(
                allowed=False,
                reason=(
                    f"open positions limit reached ({open_positions_count}/{self.max_active_trades})"
                ),
            )
        return RiskDecision(allowed=True)

    def trade_size_usd(self) -> float:
        return self.trade_amount_usd

