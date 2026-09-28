from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.backtest.account_events import TradeFilled
from xqatexp.domain.contracts import ExecutionFees, ExecutionRecord
from xqatexp.domain.enums import FillStatus, OrderSide
from xqatexp.strategy.registry import resolve_strategy_spec
from xqatexp.strategy.state import StrategyStateReducer, StrategyStateSnapshot
from xqatexp.strategy.state_io import load_strategy_state, strategy_state_value


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


def write_strategy_state(path: Path, state: StrategyStateSnapshot) -> None:
    value = strategy_state_value(state)
    SchemaRegistry().validate_json("strategy_state", value)
    path.write_bytes(canonical_json_bytes(value))


__all__ = [
    "apply_confirmed_fill",
    "initialize_strategy_state",
    "load_strategy_state",
    "write_strategy_state",
]
