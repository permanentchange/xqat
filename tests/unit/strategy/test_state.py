from datetime import date
from decimal import Decimal

from xqatexp.backtest.account_events import (
    CashInitialized,
    SplitApplied,
    StockDistributionApplied,
    TradeFilled,
)
from xqatexp.domain.contracts import ExecutionFees, ExecutionRecord
from xqatexp.domain.enums import FillStatus, OrderSide
from xqatexp.strategy.state import StrategyStateReducer


def _fill(
    side: OrderSide,
    requested: int,
    filled: int,
    price: str,
    *,
    fees: str = "0",
) -> TradeFilled:
    execution_price = Decimal(price)
    return TradeFilled(
        ExecutionRecord(
            date(2026, 9, 7),
            "600000.SH",
            side,
            requested,
            filled,
            execution_price,
            execution_price * filled,
            ExecutionFees(Decimal(fees), Decimal("0"), Decimal("0")),
            FillStatus.FILLED if requested == filled else FillStatus.PARTIALLY_FILLED,
            reference_price=execution_price,
        )
    )


def test_strategy_state_replay_is_deterministic_and_uses_actual_fill() -> None:
    reducer = StrategyStateReducer("stateful", "1.0.0")
    events = (
        CashInitialized(Decimal("100000")),
        _fill(OrderSide.BUY, 1000, 400, "10", fees="5"),
    )
    first = reducer.replay(Decimal("100000"), events)
    second = reducer.replay(Decimal("100000"), events)

    assert first == second
    position = first.positions[0]
    assert position.quantity == 400
    assert position.remaining_cost_basis == Decimal("4005")
    assert position.average_cost == Decimal("10.0125")
    assert position.last_buy_price == Decimal("10")
    assert position.cumulative_buy_notional == Decimal("4000")


def test_sell_reduces_remaining_cost_basis_proportionally() -> None:
    reducer = StrategyStateReducer("stateful", "1.0.0")
    state = reducer.replay(
        Decimal("100000"),
        (
            CashInitialized(Decimal("100000")),
            _fill(OrderSide.BUY, 400, 400, "10"),
            _fill(OrderSide.SELL, 100, 100, "12"),
        ),
    )
    position = state.positions[0]
    assert position.quantity == 300
    assert position.remaining_cost_basis == Decimal("3000")
    assert position.average_cost == Decimal("10")
    assert position.last_trade_side is OrderSide.SELL


def test_stock_distribution_and_split_preserve_economic_cost_basis() -> None:
    reducer = StrategyStateReducer("stateful", "1.0.0")
    state = reducer.replay(
        Decimal("100000"),
        (
            CashInitialized(Decimal("100000")),
            _fill(OrderSide.BUY, 100, 100, "10"),
            StockDistributionApplied("distribution", "600000.SH", 20),
            SplitApplied("split", "600000.SH", 120, 100),
        ),
    )
    position = state.positions[0]
    assert position.quantity == 240
    assert position.remaining_cost_basis == Decimal("1000")
    assert position.average_cost == Decimal("1000") / Decimal("240")
