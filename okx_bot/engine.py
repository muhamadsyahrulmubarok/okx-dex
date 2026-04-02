"""Trading engine with signal checks and position/risk control."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable

from okx_bot.client import OkxClient
from okx_bot.config import BotConfig, TokenRule
from okx_bot.domain import Position
from okx_bot.risk import RiskController
from okx_bot.storage import PositionStore
from okx_bot.telegram import Notifier, NullNotifier

logger = logging.getLogger(__name__)

TradeEventCallback = Callable[[dict[str, Any]], None]
ErrorEventCallback = Callable[[dict[str, Any]], None]


@dataclass
class SymbolSnapshot:
    current_price: float
    reference_price: float | None


class TradeExecutor:
    def __init__(self, client: OkxClient, dry_run: bool = False) -> None:
        self.client = client
        self.dry_run = dry_run

    def estimate_base_quantity(self, amount_usd: float, price: float) -> float:
        if price <= 0:
            raise ValueError("Price must be > 0")
        return amount_usd / price

    def buy(self, symbol: str, amount_usd: float, price: float) -> tuple[float, dict[str, Any]]:
        estimated_qty = self.estimate_base_quantity(amount_usd, price)
        if self.dry_run:
            logger.info("[DRY-RUN] BUY %s notional_usd=%s @ %s", symbol, amount_usd, price)
            return estimated_qty, {
                "dry_run": True,
                "side": "buy",
                "symbol": symbol,
                "notional_usd": amount_usd,
                "estimated_quantity": estimated_qty,
            }
        # For spot market BUY on OKX, quote_ccy means sz is quote amount (e.g. 100 USDT).
        order_result = self.client.place_market_order(symbol, "buy", amount_usd, tgt_ccy="quote_ccy")
        filled_qty = estimated_qty
        try:
            data = order_result.get("data") or []
            if data and data[0].get("fillSz"):
                filled_qty = float(data[0]["fillSz"])
        except (TypeError, ValueError):
            logger.debug("Could not parse fillSz from order response for %s", symbol)
        return filled_qty, order_result

    def sell(self, symbol: str, quantity: float) -> dict[str, Any]:
        if self.dry_run:
            logger.info("[DRY-RUN] SELL %s qty=%s", symbol, quantity)
            return {"dry_run": True, "side": "sell", "symbol": symbol, "quantity": quantity}
        return self.client.place_market_order(symbol, "sell", quantity)


class PositionManager:
    def __init__(self, store: PositionStore) -> None:
        self.store = store
        self.positions = self.store.list_positions()

    def count(self) -> int:
        return len(self.positions)

    def get(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def open_position(self, position: Position) -> None:
        self.positions[position.symbol] = position
        self.store.upsert_position(position)

    def close_position(self, symbol: str) -> None:
        self.positions.pop(symbol, None)
        self.store.delete_position(symbol)

    def snapshot(self) -> dict[str, Position]:
        return dict(self.positions)


class SignalEngine:
    def should_buy(self, token: TokenRule, snapshot: SymbolSnapshot) -> tuple[bool, str | None]:
        price = snapshot.current_price
        if token.buy_price is not None and price <= token.buy_price:
            return True, f"buy_price trigger ({price:.6f} <= {token.buy_price:.6f})"

        if token.buy_drop_pct is not None and snapshot.reference_price is not None:
            base_price = snapshot.reference_price
            if base_price > 0:
                drop_pct = ((price - base_price) / base_price) * 100
                if drop_pct <= token.buy_drop_pct:
                    return (
                        True,
                        f"buy_drop_pct trigger ({drop_pct:.2f}% <= {token.buy_drop_pct:.2f}%)",
                    )
        return False, None

    def should_sell(
        self,
        token: TokenRule,
        position: Position,
        current_price: float,
    ) -> tuple[bool, str | None]:
        pnl_pct = position.pnl_pct(current_price)
        if token.stop_loss_pct is not None and pnl_pct <= token.stop_loss_pct:
            return True, f"stop_loss trigger ({pnl_pct:.2f}% <= {token.stop_loss_pct:.2f}%)"

        if token.sell_price is not None and current_price >= token.sell_price:
            return True, f"sell_price trigger ({current_price:.6f} >= {token.sell_price:.6f})"

        if token.sell_profit_pct is not None and pnl_pct >= token.sell_profit_pct:
            return True, f"sell_profit_pct trigger ({pnl_pct:.2f}% >= {token.sell_profit_pct:.2f}%)"

        return False, None


class BotRunner:
    def __init__(
        self,
        config: BotConfig,
        client: OkxClient,
        store: PositionStore,
        *,
        dry_run: bool = False,
        notifier: Notifier | None = None,
        on_trade_event: TradeEventCallback | None = None,
        on_error_event: ErrorEventCallback | None = None,
    ) -> None:
        self.position_manager = PositionManager(store)
        self.reference_prices: dict[str, float] = {}
        self.signal_engine = SignalEngine()
        self.config = config
        self.client = client
        self.trade_executor = TradeExecutor(client, dry_run=dry_run)
        self.risk_controller = RiskController(
            max_active_trades=config.max_active_trades,
            trade_amount_usd=config.trade_amount_usd,
        )
        self.notifier = notifier or NullNotifier()
        self.on_trade_event = on_trade_event
        self.on_error_event = on_error_event

    def update_runtime(
        self,
        *,
        config: BotConfig,
        client: OkxClient,
        dry_run: bool,
        notifier: Notifier | None,
    ) -> None:
        self.config = config
        self.client = client
        self.trade_executor = TradeExecutor(client, dry_run=dry_run)
        self.risk_controller = RiskController(
            max_active_trades=config.max_active_trades,
            trade_amount_usd=config.trade_amount_usd,
        )
        self.notifier = notifier or NullNotifier()
        self._prune_reference_prices()

    def _prune_reference_prices(self) -> None:
        valid_symbols = {token.symbol for token in self.config.tokens}
        for symbol in list(self.reference_prices):
            if symbol not in valid_symbols:
                self.reference_prices.pop(symbol, None)

    def _notify(self, message: str) -> None:
        if not self.notifier.enabled:
            return
        try:
            self.notifier.send(message)
        except Exception:
            logger.exception("Failed to send Telegram notification")

    def _emit_trade_event(self, event: dict[str, Any]) -> None:
        if self.on_trade_event is None:
            return
        try:
            self.on_trade_event(event)
        except Exception:
            logger.exception("Failed to emit trade event")

    def _emit_error_event(self, event: dict[str, Any]) -> None:
        if self.on_error_event is None:
            return
        try:
            self.on_error_event(event)
        except Exception:
            logger.exception("Failed to emit error event")

    def _load_price(self, symbol: str) -> float:
        return self.client.get_last_price(symbol)

    def run_cycle(self) -> None:
        for token in self.config.tokens:
            symbol = token.symbol
            try:
                current_price = self._load_price(symbol)

                if symbol not in self.reference_prices:
                    self.reference_prices[symbol] = current_price
                reference_price = (
                    token.buy_drop_reference_price
                    if token.buy_drop_reference_price is not None
                    else self.reference_prices.get(symbol)
                )

                snapshot = SymbolSnapshot(
                    current_price=current_price,
                    reference_price=reference_price,
                )

                position = self.position_manager.get(symbol)
                if position is None:
                    risk_decision = self.risk_controller.can_open_new_trade(
                        self.position_manager.count()
                    )
                    if not risk_decision.allowed:
                        logger.debug("Skipping buy for %s: %s", symbol, risk_decision.reason)
                        continue

                    should_buy, reason = self.signal_engine.should_buy(token, snapshot)
                    if should_buy:
                        trade_size_usd = self.risk_controller.trade_size_usd()
                        qty, order_result = self.trade_executor.buy(
                            symbol=symbol,
                            amount_usd=trade_size_usd,
                            price=current_price,
                        )
                        self.position_manager.open_position(
                            Position(
                                symbol=symbol,
                                entry_price=current_price,
                                quantity=qty,
                                entry_value_usd=trade_size_usd,
                            )
                        )
                        logger.info(
                            "BUY %s @ %.6f qty=%s size_usd=%.2f reason=%s",
                            symbol,
                            current_price,
                            qty,
                            trade_size_usd,
                            reason,
                        )
                        self._notify(
                            (
                                f"BUY {symbol}\n"
                                f"Price: {current_price:.6f}\n"
                                f"Qty: {qty:.8f}\n"
                                f"Size: ${trade_size_usd:.2f}\n"
                                f"Reason: {reason}\n"
                                f"Mode: {'DRY-RUN' if self.trade_executor.dry_run else 'LIVE'}"
                            )
                        )
                        self._emit_trade_event(
                            {
                                "symbol": symbol,
                                "action": "BUY",
                                "price": current_price,
                                "quantity": qty,
                                "notional_usd": trade_size_usd,
                                "reason": reason,
                                "mode": "DRY-RUN" if self.trade_executor.dry_run else "LIVE",
                                "raw_order": order_result,
                            }
                        )
                        logger.debug("Order response: %s", order_result)
                        self.reference_prices[symbol] = current_price
                else:
                    should_sell, reason = self.signal_engine.should_sell(
                        token, position, current_price
                    )
                    if should_sell:
                        order_result = self.trade_executor.sell(symbol, position.quantity)
                        self.position_manager.close_position(symbol)
                        logger.info(
                            "SELL %s @ %.6f qty=%s pnl=%.2f%% reason=%s",
                            symbol,
                            current_price,
                            position.quantity,
                            position.pnl_pct(current_price),
                            reason,
                        )
                        self._notify(
                            (
                                f"SELL {symbol}\n"
                                f"Price: {current_price:.6f}\n"
                                f"Qty: {position.quantity:.8f}\n"
                                f"PnL: {position.pnl_pct(current_price):.2f}%\n"
                                f"Reason: {reason}\n"
                                f"Mode: {'DRY-RUN' if self.trade_executor.dry_run else 'LIVE'}"
                            )
                        )
                        self._emit_trade_event(
                            {
                                "symbol": symbol,
                                "action": "SELL",
                                "price": current_price,
                                "quantity": position.quantity,
                                "notional_usd": current_price * position.quantity,
                                "pnl_pct": position.pnl_pct(current_price),
                                "reason": reason,
                                "mode": "DRY-RUN" if self.trade_executor.dry_run else "LIVE",
                                "raw_order": order_result,
                            }
                        )
                        logger.debug("Order response: %s", order_result)
                        self.reference_prices[symbol] = current_price
            except Exception:
                logger.exception("Error processing symbol %s", symbol)
                self._emit_error_event(
                    {
                        "symbol": symbol,
                        "message": "Error while processing symbol",
                        "mode": "DRY-RUN" if self.trade_executor.dry_run else "LIVE",
                    }
                )
                self._notify(
                    f"ERROR processing {symbol}\nCheck bot logs for details.\nMode: {'DRY-RUN' if self.trade_executor.dry_run else 'LIVE'}"
                )

    def positions_report(self) -> dict[str, dict[str, float | str]]:
        report: dict[str, dict[str, float | str]] = {}
        for symbol, pos in self.position_manager.snapshot().items():
            report[symbol] = {
                "entry_price": pos.entry_price,
                "quantity": pos.quantity,
                "entry_value_usd": pos.entry_value_usd,
                "opened_at": pos.opened_at,
            }
        return report

