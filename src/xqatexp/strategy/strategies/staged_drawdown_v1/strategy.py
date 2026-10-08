from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any, cast

from xqatexp.domain.contracts import (
    CustomFactorView,
    ResearchDataSlice,
    ResearchDataView,
    StrategyDiagnostics,
)
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.decision import stable_decision_id, stable_intent_id
from xqatexp.strategy.intents import (
    CurrentPositionFraction,
    FixedNotional,
    FixedQuantity,
    FullPosition,
    InitialCapitalFraction,
    IntentSizing,
    TradeIntent,
    TradeIntentDecision,
)
from xqatexp.strategy.state import StrategyPositionState, StrategyStateView

from .declaration import staged_drawdown_declaration
from .parameters import StagedDrawdownParameters


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
        if len(days) < values.required_history_days:
            raise ValueError("STRATEGY_WARMUP_INSUFFICIENT: staged drawdown lookback is incomplete")
        recent = days[-values.required_history_days :]
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
        if tuple(row.get("trade_date") for row in rows) != recent or any(
            row.get("research_close") is None or row.get("close_raw") is None for row in rows
        ):
            raise ValueError("DATA_REQUIRED_MISSING: staged drawdown price history")
        closes = tuple(Decimal(str(row["research_close"])) for row in rows)
        raw_closes = tuple(Decimal(str(row["close_raw"])) for row in rows)
        if any(not value.is_finite() or value <= 0 for value in (*closes, *raw_closes)):
            raise ValueError("DATA_REQUIRED_MISSING: nonpositive staged drawdown price")

        cumulative_return, worst_daily_return, down_days, slow_decline = self._decline_metrics(
            closes[-values.lookback_trade_days :]
        )

        current_close = raw_closes[-1]
        position = state.position(values.security_id)
        quantity = 0 if position is None else position.quantity
        entry_allowed = slow_decline
        entry_diagnostics: dict[str, object] = {
            "entry_confirmation_mode": values.entry_confirmation_mode
        }
        if values.entry_confirmation_mode == "ma_rebound":
            entry_allowed, entry_diagnostics = self._entry_confirmation(recent, closes, position)
        remaining = max(
            Decimal("0"),
            state.initial_capital * values.max_capital_fraction
            - (
                Decimal("0")
                if position is None or quantity == 0
                else position.cumulative_buy_notional
            ),
        )
        add_decline_reached = (
            quantity > 0
            and position is not None
            and position.last_buy_price is not None
            and current_close <= position.last_buy_price * (1 - values.add_buy_decline_threshold)
        )
        after_sell = position is not None and position.last_trade_side is OrderSide.SELL
        add_allowed = add_decline_reached and (
            position is not None
            and (
                position.last_trade_side is OrderSide.BUY
                or (after_sell and values.allow_add_after_sell)
            )
        )
        profit_rate: Decimal | None = None
        if quantity > 0:
            if position is None or position.remaining_cost_basis <= 0:
                raise ValueError("STRATEGY_STATE_INVALID: positive position has no cost basis")
            profit_rate = current_close * Decimal(
                quantity
            ) / position.remaining_cost_basis - Decimal("1")

        signal = "NONE"
        sizing: IntentSizing | None = None
        side: OrderSide | None = None
        tier_diagnostics: dict[str, object] = {}
        sell_sizing: CurrentPositionFraction | FixedQuantity | FullPosition | None = None
        if values.take_profit_mode == "tiered":
            sell_sizing, tier_diagnostics = self._tiered_exit(view, position, profit_rate)
        elif (
            quantity > 0 and profit_rate is not None and profit_rate >= values.take_profit_threshold
        ):
            sell_sizing = CurrentPositionFraction(values.sell_fraction)
        if sell_sizing is not None:
            signal = (
                f"TAKE_PROFIT_TIER_{tier_diagnostics['eligible_profit_tier']}"
                if values.take_profit_mode == "tiered"
                else "TAKE_PROFIT"
            )
            side = OrderSide.SELL
            sizing = sell_sizing
        elif quantity == 0 and entry_allowed:
            signal = "INITIAL_ENTRY"
            side = OrderSide.BUY
            sizing = InitialCapitalFraction(values.buy_fraction)
        elif add_allowed:
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
                "take_profit_mode": values.take_profit_mode,
                **tier_diagnostics,
                **entry_diagnostics,
                "allow_add_after_sell": values.allow_add_after_sell,
                "last_trade_side": None
                if position is None or position.last_trade_side is None
                else position.last_trade_side.value,
                "add_decline_reached": add_decline_reached,
                "add_blocked_after_sell": add_decline_reached
                and after_sell
                and not values.allow_add_after_sell
                and remaining > 0,
                "add_budget_remaining": remaining,
                "add_budget_exhausted": add_decline_reached and remaining == 0,
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

    def _decline_metrics(self, closes: Sequence[Decimal]) -> tuple[Decimal, Decimal, int, bool]:
        returns = tuple(closes[index] / closes[index - 1] - 1 for index in range(1, len(closes)))
        cumulative_return = closes[-1] / closes[0] - 1
        worst_daily_return = min(returns)
        down_days = sum(value < 0 for value in returns)
        values = self.parameters
        slow_decline = (
            cumulative_return <= -values.cumulative_decline_threshold
            and worst_daily_return >= -values.single_day_crash_threshold
            and down_days >= values.minimum_down_days
        )
        return cumulative_return, worst_daily_return, down_days, slow_decline

    def _entry_confirmation(
        self,
        days: Sequence[date],
        closes: Sequence[Decimal],
        position: StrategyPositionState | None,
    ) -> tuple[bool, dict[str, object]]:
        values = self.parameters
        ma = sum(closes[-values.entry_confirmation_ma_days :]) / values.entry_confirmation_ma_days
        above_ma = closes[-1] > ma
        price_up = closes[-1] > closes[-2]
        passed = above_ma and price_up
        setup_date: date | None = None
        setup_age: int | None = None
        flat = position is None or position.quantity == 0
        if flat:
            for age in range(values.entry_confirmation_window_days + 1):
                index = len(closes) - 1 - age
                if (
                    position is not None
                    and position.last_trade_date is not None
                    and days[index] < position.last_trade_date
                ):
                    break
                if self._decline_metrics(
                    closes[index - values.lookback_trade_days + 1 : index + 1]
                )[-1]:
                    setup_date, setup_age = days[index], age
                    break
        return flat and setup_date is not None and passed, {
            "entry_confirmation_mode": values.entry_confirmation_mode,
            "entry_setup_date": setup_date,
            "entry_setup_age_trade_days": setup_age,
            "entry_confirmation_ma": ma,
            "entry_confirmation_above_ma": above_ma,
            "entry_confirmation_price_up": price_up,
            "entry_confirmation_passed": passed,
            "entry_waiting": flat and setup_date is not None and not passed,
        }

    def _tiered_exit(
        self,
        view: ResearchDataSlice,
        position: StrategyPositionState | None,
        profit_rate: Decimal | None,
    ) -> tuple[FixedQuantity | FullPosition | None, dict[str, object]]:
        diagnostics: dict[str, object] = {
            "eligible_profit_tier": 0,
            "exit_base_quantity": None,
            "exit_base_locked": False,
            "exit_sold_quantity": Decimal("0"),
            "tier_cumulative_target_quantity": 0,
            "tier_remaining_quantity": 0,
            "tier_full_exit": False,
        }
        if position is None or position.quantity == 0:
            return None, diagnostics
        if position.exit_base_quantity is None and position.last_trade_side is OrderSide.SELL:
            raise ValueError(
                "STRATEGY_STATE_INVALID: tiered exit history missing; replay confirmed fills"
            )
        base = position.exit_base_quantity or Decimal(position.quantity)
        sold = position.exit_sold_quantity
        diagnostics.update(
            exit_base_quantity=base,
            exit_base_locked=position.exit_base_quantity is not None,
            exit_sold_quantity=sold,
        )
        rules = cast(
            Sequence[Mapping[str, Any]],
            view.security_rules((position.security_id,), ("sell_lot_size",)),
        )
        if len(rules) != 1:
            raise ValueError("DATA_REQUIRED_MISSING: tiered sell lot size")
        lot = rules[0].get("sell_lot_size")
        if isinstance(lot, bool) or not isinstance(lot, int) or lot <= 0:
            raise ValueError("DATA_REQUIRED_MISSING: invalid tiered sell lot size")
        eligible = [
            index
            for index, threshold in enumerate(self.parameters.take_profit_levels)
            if profit_rate is not None and profit_rate >= threshold
        ]
        if not eligible:
            return None, diagnostics
        tier = eligible[-1]
        diagnostics["eligible_profit_tier"] = tier + 1
        if tier == len(self.parameters.take_profit_levels) - 1:
            diagnostics.update(
                tier_cumulative_target_quantity=base,
                tier_remaining_quantity=position.quantity,
                tier_full_exit=True,
            )
            return FullPosition(), diagnostics

        fraction = sum(self.parameters.take_profit_sell_fractions[: tier + 1])
        target = int((base * fraction / lot).to_integral_value(rounding=ROUND_FLOOR)) * lot
        remaining = Decimal(target) - sold
        diagnostics["tier_cumulative_target_quantity"] = target
        if remaining <= 0 and not (target == 0 and sold == 0):
            return None, diagnostics
        if remaining < lot:
            diagnostics.update(tier_remaining_quantity=position.quantity, tier_full_exit=True)
            return FullPosition(), diagnostics
        quantity = int((remaining / lot).to_integral_value(rounding=ROUND_FLOOR)) * lot
        diagnostics["tier_remaining_quantity"] = quantity
        return FixedQuantity(quantity), diagnostics

    @staticmethod
    def _next_day(research: ResearchDataView, decision: date) -> date:
        method = getattr(research, "next_trading_day", None)
        if callable(method):
            return cast(date, method(decision))
        candidate = decision + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
