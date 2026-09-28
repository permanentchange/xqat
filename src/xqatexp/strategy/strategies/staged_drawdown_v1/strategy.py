from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

from xqatexp.domain.contracts import CustomFactorView, ResearchDataView, StrategyDiagnostics
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.decision import stable_decision_id, stable_intent_id
from xqatexp.strategy.intents import (
    CurrentPositionFraction,
    FixedNotional,
    InitialCapitalFraction,
    TradeIntent,
    TradeIntentDecision,
)
from .declaration import staged_drawdown_declaration
from .parameters import StagedDrawdownParameters
from xqatexp.strategy.state import StrategyStateView


class StagedDrawdownStrategy:
    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.parameters = StagedDrawdownParameters.from_mapping(parameters)
        self._parameter_values = self.parameters.as_mapping()
        self.declaration = staged_drawdown_declaration(self._parameter_values)

    def generate_stateful_decision(
        self,
        research: ResearchDataView,
        state: StrategyStateView,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
    ) -> TradeIntentDecision:
        del custom, parameters
        values = self.parameters
        days = tuple(research.trading_days(research.earliest_date, research.decision_date))
        if len(days) < values.lookback_trade_days:
            raise ValueError(
                "STRATEGY_WARMUP_INSUFFICIENT: staged drawdown lookback is incomplete"
            )
        recent = days[-values.lookback_trade_days :]
        view = research.slice(research.decision_date)
        rows = cast(
            Sequence[Mapping[str, Any]],
            view.history(
                (values.security_id,),
                ("research_close", "close_raw"),
                recent[0],
                recent[-1],
            ),
        )
        if len(rows) != values.lookback_trade_days or any(
            row.get("research_close") is None or row.get("close_raw") is None for row in rows
        ):
            raise ValueError("DATA_REQUIRED_MISSING: staged drawdown price history")
        closes = tuple(Decimal(str(row["research_close"])) for row in rows)
        raw_closes = tuple(Decimal(str(row["close_raw"])) for row in rows)
        if any(value <= 0 for value in (*closes, *raw_closes)):
            raise ValueError("DATA_REQUIRED_MISSING: nonpositive staged drawdown price")

        returns = tuple(closes[index] / closes[index - 1] - 1 for index in range(1, len(closes)))
        cumulative_return = closes[-1] / closes[0] - 1
        worst_daily_return = min(returns)
        down_days = sum(value < 0 for value in returns)
        slow_decline = (
            cumulative_return <= -values.cumulative_decline_threshold
            and worst_daily_return >= -values.single_day_crash_threshold
            and down_days >= values.minimum_down_days
        )

        current_close = raw_closes[-1]
        position = state.position(values.security_id)
        quantity = 0 if position is None else position.quantity
        profit_rate: Decimal | None = None
        if quantity > 0:
            if position is None or position.remaining_cost_basis <= 0:
                raise ValueError("STRATEGY_STATE_INVALID: positive position has no cost basis")
            profit_rate = (
                current_close * Decimal(quantity) / position.remaining_cost_basis - Decimal("1")
            )

        signal = "NONE"
        sizing: InitialCapitalFraction | FixedNotional | CurrentPositionFraction | None = None
        side: OrderSide | None = None
        if (
            quantity > 0
            and profit_rate is not None
            and profit_rate >= values.take_profit_threshold
        ):
            signal = "TAKE_PROFIT"
            side = OrderSide.SELL
            sizing = CurrentPositionFraction(values.sell_fraction)
        elif quantity == 0 and slow_decline:
            signal = "INITIAL_ENTRY"
            side = OrderSide.BUY
            sizing = InitialCapitalFraction(values.buy_fraction)
        elif (
            quantity > 0
            and position is not None
            and position.last_trade_side is OrderSide.BUY
            and position.last_buy_price is not None
            and current_close
            <= position.last_buy_price * (Decimal("1") - values.add_buy_decline_threshold)
        ):
            maximum = state.initial_capital * values.max_capital_fraction
            remaining = maximum - position.cumulative_buy_notional
            standard = state.initial_capital * values.buy_fraction
            if remaining > 0:
                signal = "ADD_ON_DECLINE"
                side = OrderSide.BUY
                sizing = (
                    InitialCapitalFraction(values.buy_fraction)
                    if remaining >= standard
                    else FixedNotional(remaining)
                )

        effective = self._next_day(research, research.decision_date)
        decision_id = stable_decision_id(
            self.declaration.strategy_id,
            self.declaration.strategy_version,
            research.decision_date,
            effective,
            "TRADE_INTENT",
        )
        intents: tuple[TradeIntent, ...] = ()
        if side is not None and sizing is not None:
            intents = (
                TradeIntent(
                    stable_intent_id(decision_id, 0, values.security_id, side.value),
                    values.security_id,
                    side,
                    sizing,
                    (signal,),
                    0,
                ),
            )
        diagnostics = StrategyDiagnostics(
            "staged_drawdown_v1",
            "1.0",
            {
                "security_id": values.security_id,
                "signal": signal,
                "current_raw_close": current_close,
                "current_research_close": closes[-1],
                "lookback_cumulative_return": cumulative_return,
                "worst_daily_return": worst_daily_return,
                "down_days": down_days,
                "slow_decline": slow_decline,
                "position_quantity": quantity,
                "position_profit_rate": profit_rate,
                "last_buy_price": None if position is None else position.last_buy_price,
                "cumulative_buy_notional": (
                    Decimal("0") if position is None else position.cumulative_buy_notional
                ),
            },
        )
        return TradeIntentDecision(
            decision_id,
            self.declaration.strategy_id,
            self.declaration.strategy_version,
            research.decision_date,
            effective,
            intents,
            diagnostics,
        )

    @staticmethod
    def _next_day(research: ResearchDataView, decision: date) -> date:
        method = getattr(research, "next_trading_day", None)
        if callable(method):
            return cast(date, method(decision))
        candidate = decision + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
