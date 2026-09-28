from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol

from xqatexp.backtest.account import SimulatedAccount
from xqatexp.backtest.execution import ExecutionFacts, ExecutionOutcome, ExecutionSimulator
from xqatexp.backtest.fees import FeeModel
from xqatexp.domain.contracts import AllocationDecision, ExecutionRecord, RebalanceInstruction
from xqatexp.domain.enums import AssetType, OrderSide, UnfilledReason
from xqatexp.portfolio.rebalance import LotRule, RebalancePlanner
from xqatexp.strategy.decision import stable_instruction_id


class ExecutionData(Protocol):
    def next_trading_day(self, after: date) -> date: ...

    def execution_rows(
        self,
        execution_date: date,
        security_ids: Sequence[str],
    ) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class UnfilledRecord:
    decision_date: date
    execution_date: date
    security_id: str
    side: OrderSide
    requested_quantity: int
    filled_quantity: int
    unfilled_quantity: int
    reason: UnfilledReason


@dataclass(frozen=True, slots=True)
class DecisionExecutionResult:
    trades: tuple[tuple[ExecutionRecord, AssetType], ...]
    unfilled: tuple[UnfilledRecord, ...]
    two_way_adjustment_turnover: Decimal
    turnover_base: Decimal


class AllocationDecisionExecutor:
    def __init__(self, fees: FeeModel | None = None) -> None:
        self._fees = fees or FeeModel.default()
        self._execution = ExecutionSimulator(self._fees)

    def execute(
        self,
        *,
        data: ExecutionData,
        account: SimulatedAccount,
        decision: AllocationDecision,
        execution_date: date,
        slippage: Decimal,
        participation: Decimal,
    ) -> DecisionExecutionResult:
        target = decision.target
        security_ids = sorted(
            set(account.positions) | {position.security_id for position in target.positions}
        )
        rows = self._rows_by_security(data, execution_date, security_ids)
        prices = {security_id: self._reference_price(row) for security_id, row in rows.items()}
        rules = {
            security_id: LotRule(int(row["buy_lot_size"]), int(row["sell_lot_size"]))
            for security_id, row in rows.items()
        }
        assets = {
            security_id: AssetType(str(row["asset_type"])) for security_id, row in rows.items()
        }

        def estimate(security_id: str, side: OrderSide, quantity: int, price: Decimal) -> Decimal:
            gross = Decimal(quantity) * price
            return self._fees.calculate(assets[security_id], side, gross, execution_date).total

        planner = RebalancePlanner(estimate)
        open_value = (
            account.cash_available
            + account.cash_receivable
            + sum(
                (
                    Decimal(quantity) * prices[security_id]
                    for security_id, quantity in account.positions.items()
                ),
                Decimal("0"),
            )
        )
        before_positions = dict(account.positions)
        before_cash = account.cash_available + account.cash_receivable
        unfilled: list[UnfilledRecord] = []
        executed: list[tuple[ExecutionRecord, AssetType]] = []

        sell_plan = planner.plan(
            target,
            current_positions=account.positions,
            sellable_quantities=account.sellable_quantities,
            portfolio_value=open_value,
            reference_prices=prices,
            lot_rules=rules,
            cash_budget=Decimal("0"),
        )
        for raw_instruction in sell_plan.instructions:
            if raw_instruction.side is OrderSide.SELL:
                self._execute_instruction(
                    data=data,
                    account=account,
                    decision=decision,
                    execution_date=execution_date,
                    instruction=self._identified_instruction(decision, raw_instruction),
                    row=rows[raw_instruction.security_id],
                    asset_type=assets[raw_instruction.security_id],
                    slippage=slippage,
                    participation=participation,
                    executed=executed,
                    unfilled=unfilled,
                )

        buy_plan = planner.plan(
            target,
            current_positions=account.positions,
            sellable_quantities=account.sellable_quantities,
            portfolio_value=open_value,
            reference_prices=prices,
            lot_rules=rules,
            cash_budget=account.cash_available,
        )
        for raw_instruction in buy_plan.instructions:
            if raw_instruction.side is OrderSide.BUY:
                self._execute_instruction(
                    data=data,
                    account=account,
                    decision=decision,
                    execution_date=execution_date,
                    instruction=self._identified_instruction(decision, raw_instruction),
                    row=rows[raw_instruction.security_id],
                    asset_type=assets[raw_instruction.security_id],
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

    @staticmethod
    def _identified_instruction(
        decision: AllocationDecision,
        instruction: RebalanceInstruction,
    ) -> RebalanceInstruction:
        return replace(
            instruction,
            instruction_id=stable_instruction_id(
                decision.decision_id,
                instruction.security_id,
                instruction.side.value,
            ),
            decision_id=decision.decision_id,
        )

    def _execute_instruction(
        self,
        *,
        data: ExecutionData,
        account: SimulatedAccount,
        decision: AllocationDecision,
        execution_date: date,
        instruction: RebalanceInstruction,
        row: Mapping[str, Any],
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
                    decision.target.decision_date,
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

        boundary_fallback = "valuation_close" if suspended else "high_raw"
        facts = ExecutionFacts(
            asset_type,
            self._decimal(row, "open_raw", fallback="valuation_close"),
            self._decimal(row, "high_raw", fallback="valuation_close"),
            self._decimal(row, "low_raw", fallback="valuation_close"),
            self._decimal(row, "up_limit", fallback=boundary_fallback),
            self._decimal(
                row,
                "down_limit",
                fallback="valuation_close" if suspended else "low_raw",
            ),
            int(row["volume_shares"]),
            self._decimal(row, "price_tick"),
            bool(row.get("is_suspended_full_day")),
            bool(row.get("is_limit_up_locked")),
            bool(row.get("is_limit_down_locked")),
        )
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
                    decision.target.decision_date,
                    execution_date,
                    instruction.security_id,
                    instruction.side,
                    instruction.requested_quantity,
                    filled,
                    outcome.unfilled_quantity,
                    outcome.unfilled_reason,
                )
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
        security_ids: Sequence[str],
    ) -> dict[str, Mapping[str, Any]]:
        rows = {str(row["security_id"]): row for row in data.execution_rows(on_date, security_ids)}
        missing = set(security_ids) - set(rows)
        if missing:
            raise ValueError(f"BACKTEST_VALUATION_MISSING: {min(missing)} on {on_date}")
        return rows

    @classmethod
    def _reference_price(cls, row: Mapping[str, Any]) -> Decimal:
        return cls._decimal(row, "open_raw", fallback="valuation_close")

    @staticmethod
    def _decimal(
        row: Mapping[str, Any],
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
