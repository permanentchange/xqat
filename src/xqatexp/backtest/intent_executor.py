from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal

from xqatexp.backtest.account import SimulatedAccount
from xqatexp.backtest.decision_executor import (
    DecisionExecutionResult,
    ExecutionData,
    UnfilledRecord,
)
from xqatexp.backtest.execution import ExecutionFacts, ExecutionOutcome, ExecutionSimulator
from xqatexp.backtest.fees import FeeModel
from xqatexp.domain.contracts import ExecutionInstruction, ExecutionRecord
from xqatexp.domain.enums import AssetType, OrderSide, UnfilledReason
from xqatexp.strategy.decision import stable_instruction_id
from xqatexp.strategy.intents import (
    CurrentPositionFraction,
    FixedNotional,
    FullPosition,
    InitialCapitalFraction,
    TradeIntent,
    TradeIntentDecision,
)
from xqatexp.strategy.state import StrategyStateView


class IntentExecutor:
    def __init__(self, fees: FeeModel | None = None) -> None:
        self._fees = fees or FeeModel.default()
        self._execution = ExecutionSimulator(self._fees)

    def execute(
        self,
        *,
        data: ExecutionData,
        account: SimulatedAccount,
        decision: TradeIntentDecision,
        state: StrategyStateView,
        execution_date: date,
        slippage: Decimal,
        participation: Decimal,
    ) -> DecisionExecutionResult:
        security_ids = sorted(
            set(account.positions) | {intent.security_id for intent in decision.intents}
        )
        rows = self._rows_by_security(data, execution_date, security_ids)
        prices = {security_id: self._reference_price(row) for security_id, row in rows.items()}
        before_positions = dict(account.positions)
        before_cash = account.cash_available + account.cash_receivable
        open_value = (
            before_cash
            + sum(
                (
                    Decimal(quantity) * prices[security_id]
                    for security_id, quantity in before_positions.items()
                ),
                Decimal("0"),
            )
        )

        executed: list[tuple[ExecutionRecord, AssetType]] = []
        unfilled: list[UnfilledRecord] = []
        ordered = sorted(
            decision.intents,
            key=lambda item: (
                0 if item.side is OrderSide.SELL else 1,
                item.priority,
                item.security_id,
                item.intent_id,
            ),
        )
        for intent in ordered:
            row = rows[intent.security_id]
            asset_type = AssetType(str(row["asset_type"]))
            facts = self._execution_facts(row, asset_type)
            instruction = self._instruction(
                decision,
                intent,
                account,
                state,
                row,
                facts,
                slippage,
            )
            if instruction is None:
                continue
            self._execute_instruction(
                data=data,
                account=account,
                decision=decision,
                execution_date=execution_date,
                instruction=instruction,
                row=row,
                facts=facts,
                asset_type=asset_type,
                slippage=slippage,
                participation=participation,
                executed=executed,
                unfilled=unfilled,
            )

        two_way = self._adjustment_turnover(
            before_positions,
            before_cash,
            account.positions,
            account.cash_available + account.cash_receivable,
            prices,
        )
        return DecisionExecutionResult(
            tuple(executed),
            tuple(unfilled),
            two_way,
            open_value,
        )

    def _instruction(
        self,
        decision: TradeIntentDecision,
        intent: TradeIntent,
        account: SimulatedAccount,
        state: StrategyStateView,
        row: Mapping[str, object],
        facts: ExecutionFacts,
        slippage: Decimal,
    ) -> ExecutionInstruction | None:
        price = self._reference_price(row)
        current = account.positions.get(intent.security_id, 0)
        buy_lot = int(row["buy_lot_size"])
        sell_lot = int(row["sell_lot_size"])

        if intent.side is OrderSide.BUY:
            notional = self._buy_notional(intent, state)
            modeled_price = self._execution.modeled_price(
                OrderSide.BUY,
                facts,
                slippage,
            )
            budget_price = modeled_price or price
            raw_quantity = int(
                (notional / budget_price).to_integral_value(rounding=ROUND_FLOOR)
            )
            requested = raw_quantity // buy_lot * buy_lot
            lot_size = buy_lot
            target_quantity = current + requested
        else:
            raw_quantity, full_exit = self._sell_quantity(intent, current, price, state)
            requested = (
                raw_quantity
                if full_exit
                else raw_quantity // sell_lot * sell_lot
            )
            lot_size = sell_lot
            target_quantity = max(0, current - requested)

        if requested <= 0:
            return None
        return ExecutionInstruction(
            security_id=intent.security_id,
            side=intent.side,
            current_quantity=current,
            theoretical_target_quantity=target_quantity,
            requested_quantity=requested,
            lot_size=lot_size,
            priority=intent.priority,
            reference_price=price,
            reason_codes=intent.reason_codes,
            instruction_id=stable_instruction_id(
                decision.decision_id,
                intent.security_id,
                intent.side.value,
                intent_id=intent.intent_id,
            ),
            decision_id=decision.decision_id,
        )

    @staticmethod
    def _buy_notional(
        intent: TradeIntent,
        state: StrategyStateView,
    ) -> Decimal:
        if isinstance(intent.sizing, FixedNotional):
            return intent.sizing.amount
        if isinstance(intent.sizing, InitialCapitalFraction):
            return state.initial_capital * intent.sizing.fraction
        raise ValueError("STRATEGY_INTENT_INVALID: unsupported buy sizing")

    @staticmethod
    def _sell_quantity(
        intent: TradeIntent,
        current: int,
        price: Decimal,
        state: StrategyStateView,
    ) -> tuple[int, bool]:
        del state
        if isinstance(intent.sizing, FullPosition):
            return current, True
        if isinstance(intent.sizing, CurrentPositionFraction):
            raw = int(
                (Decimal(current) * intent.sizing.fraction).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
            return raw, raw == current
        if isinstance(intent.sizing, FixedNotional):
            raw = int(
                (intent.sizing.amount / price).to_integral_value(rounding=ROUND_FLOOR)
            )
            return min(current, raw), raw >= current
        raise ValueError("STRATEGY_INTENT_INVALID: unsupported sell sizing")

    def _execute_instruction(
        self,
        *,
        data: ExecutionData,
        account: SimulatedAccount,
        decision: TradeIntentDecision,
        execution_date: date,
        instruction: ExecutionInstruction,
        row: Mapping[str, object],
        facts: ExecutionFacts,
        asset_type: AssetType,
        slippage: Decimal,
        participation: Decimal,
        executed: list[tuple[ExecutionRecord, AssetType]],
        unfilled: list[UnfilledRecord],
    ) -> None:
        suspended = bool(row.get("is_suspended_full_day"))
        if row.get("open_raw") is None and not suspended:
            unfilled.append(
                UnfilledRecord(
                    decision.decision_date,
                    execution_date,
                    instruction.security_id,
                    instruction.side,
                    instruction.requested_quantity,
                    0,
                    instruction.requested_quantity,
                    UnfilledReason.NO_EXECUTION_PRICE,
                )
            )
            return

        outcome: ExecutionOutcome = self._execution.execute(
            instruction,
            facts,
            execution_date,
            account.cash_available,
            account.positions.get(instruction.security_id, 0),
            account.sellable_quantities.get(instruction.security_id, 0),
            participation,
            slippage,
        )
        if outcome.record is not None:
            release = None
            if outcome.record.side is OrderSide.BUY:
                try:
                    release = data.next_trading_day(execution_date)
                except (IndexError, ValueError):
                    release = execution_date + timedelta(days=7)
            account.apply_trade(outcome.record, release_date=release)
            executed.append((outcome.record, asset_type))

        if outcome.unfilled_reason is not None and outcome.unfilled_quantity > 0:
            filled = outcome.record.filled_quantity if outcome.record is not None else 0
            unfilled.append(
                UnfilledRecord(
                    decision.decision_date,
                    execution_date,
                    instruction.security_id,
                    instruction.side,
                    instruction.requested_quantity,
                    filled,
                    outcome.unfilled_quantity,
                    outcome.unfilled_reason,
                )
            )

    @classmethod
    def _execution_facts(
        cls,
        row: Mapping[str, object],
        asset_type: AssetType,
    ) -> ExecutionFacts:
        suspended = bool(row.get("is_suspended_full_day"))
        boundary_fallback = "valuation_close" if suspended else "high_raw"
        return ExecutionFacts(
            asset_type,
            cls._decimal(row, "open_raw", fallback="valuation_close"),
            cls._decimal(row, "high_raw", fallback="valuation_close"),
            cls._decimal(row, "low_raw", fallback="valuation_close"),
            cls._decimal(row, "up_limit", fallback=boundary_fallback),
            cls._decimal(
                row,
                "down_limit",
                fallback="valuation_close" if suspended else "low_raw",
            ),
            int(row["volume_shares"]),
            cls._decimal(row, "price_tick"),
            suspended,
            bool(row.get("is_limit_up_locked")),
            bool(row.get("is_limit_down_locked")),
        )

    @staticmethod
    def _adjustment_turnover(
        before_positions: Mapping[str, int],
        before_cash: Decimal,
        after_positions: Mapping[str, int],
        after_cash: Decimal,
        prices: Mapping[str, Decimal],
    ) -> Decimal:
        security_ids = set(before_positions) | set(after_positions)
        before_values = {
            security_id: Decimal(before_positions.get(security_id, 0)) * prices[security_id]
            for security_id in security_ids
        }
        after_values = {
            security_id: Decimal(after_positions.get(security_id, 0)) * prices[security_id]
            for security_id in security_ids
        }
        before_total = before_cash + sum(before_values.values(), Decimal("0"))
        after_total = after_cash + sum(after_values.values(), Decimal("0"))
        if before_total <= 0 or after_total <= 0:
            raise ValueError("BACKTEST_ACCOUNT_CONSERVATION_BROKEN: invalid turnover base")
        distance = abs(after_cash / after_total - before_cash / before_total)
        for security_id in security_ids:
            distance += abs(
                after_values[security_id] / after_total - before_values[security_id] / before_total
            )
        return distance / 2

    @staticmethod
    def _rows_by_security(
        data: ExecutionData,
        on_date: date,
        security_ids: list[str],
    ) -> dict[str, Mapping[str, object]]:
        rows = {str(row["security_id"]): row for row in data.execution_rows(on_date, security_ids)}
        missing = set(security_ids) - set(rows)
        if missing:
            raise ValueError(f"BACKTEST_VALUATION_MISSING: {min(missing)} on {on_date}")
        return rows

    @classmethod
    def _reference_price(cls, row: Mapping[str, object]) -> Decimal:
        return cls._decimal(row, "open_raw", fallback="valuation_close")

    @staticmethod
    def _decimal(
        row: Mapping[str, object],
        field: str,
        *,
        fallback: str | None = None,
    ) -> Decimal:
        value = row.get(field)
        if value is None and fallback is not None:
            value = row.get(fallback)
        if value is None:
            raise ValueError(f"BACKTEST_VALUATION_MISSING: {field}")
        result = Decimal(str(value))
        if result <= 0:
            raise ValueError(f"BACKTEST_VALUATION_MISSING: nonpositive {field}")
        return result
