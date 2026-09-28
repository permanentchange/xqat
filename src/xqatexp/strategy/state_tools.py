from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from xqatexp.backtest.account_events import SplitApplied, StockDistributionApplied, TradeFilled
from xqatexp.domain.contracts import ExecutionFees, ExecutionRecord
from xqatexp.domain.enums import FillStatus, OrderSide
from xqatexp.strategy.registry import resolve_strategy_spec
from xqatexp.strategy.state import StrategyStateReducer, StrategyStateSnapshot
from xqatexp.strategy.state_io import load_strategy_state


def initialize_strategy_state(
    strategy_id: str,
    strategy_version: str,
    initial_capital: Decimal,
) -> StrategyStateSnapshot:
    resolve_strategy_spec(strategy_id, strategy_version)
    if initial_capital <= 0:
        raise ValueError("STRATEGY_STATE_INVALID: initial capital must be positive")
    return StrategyStateReducer(strategy_id, strategy_version).initial(initial_capital)


def apply_confirmed_fill(
    state: StrategyStateSnapshot,
    *,
    execution_date: date,
    security_id: str,
    side: OrderSide,
    quantity: int,
    execution_price: Decimal,
    commission: Decimal = Decimal("0"),
    transfer_fee: Decimal = Decimal("0"),
    stamp_duty: Decimal = Decimal("0"),
) -> StrategyStateSnapshot:
    if quantity <= 0:
        raise ValueError("STRATEGY_STATE_INVALID: confirmed fill quantity must be positive")
    if execution_price <= 0:
        raise ValueError("STRATEGY_STATE_INVALID: execution price must be positive")
    fees = (commission, transfer_fee, stamp_duty)
    if any(value < 0 for value in fees):
        raise ValueError("STRATEGY_STATE_INVALID: execution fees must be nonnegative")
    record = ExecutionRecord(
        execution_date=execution_date,
        security_id=security_id,
        side=side,
        requested_quantity=quantity,
        filled_quantity=quantity,
        execution_price=execution_price,
        gross_amount=execution_price * Decimal(quantity),
        fees=ExecutionFees(commission, transfer_fee, stamp_duty),
        status=FillStatus.FILLED,
        reference_price=execution_price,
    )
    reducer = StrategyStateReducer(state.strategy_id, state.strategy_version)
    return reducer.apply(state, TradeFilled(record))


def apply_confirmed_stock_adjustment(
    state: StrategyStateSnapshot,
    *,
    effective_date: date,
    event_id: str,
    security_id: str,
    added_quantity: int,
    kind: str,
) -> StrategyStateSnapshot:
    if state.as_of is not None and effective_date < state.as_of:
        raise ValueError("STRATEGY_STATE_INVALID: adjustment date precedes state as_of")
    if added_quantity <= 0:
        raise ValueError("STRATEGY_STATE_INVALID: stock adjustment quantity must be positive")
    reducer = StrategyStateReducer(state.strategy_id, state.strategy_version)
    if kind == "STOCK_DISTRIBUTION":
        event = StockDistributionApplied(event_id, security_id, added_quantity)
    elif kind == "SPLIT":
        event = SplitApplied(event_id, security_id, added_quantity, 0)
    else:
        raise ValueError("STRATEGY_STATE_INVALID: unsupported stock adjustment kind")
    updated = reducer.apply(state, event)
    return replace(updated, as_of=effective_date)


__all__ = [
    "apply_confirmed_fill",
    "apply_confirmed_stock_adjustment",
    "initialize_strategy_state",
    "load_strategy_state",
]
