from datetime import date
from decimal import Decimal

import pytest

from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.state_tools import apply_confirmed_fill, initialize_strategy_state


def test_state_tools_initialize_registered_strategy_and_apply_actual_fill() -> None:
    state = initialize_strategy_state(
        "staged_drawdown_v1",
        "1.0.0",
        Decimal("100000"),
    )
    state = apply_confirmed_fill(
        state,
        execution_date=date(2026, 9, 7),
        security_id="600000.SH",
        side=OrderSide.BUY,
        quantity=1000,
        execution_price=Decimal("10"),
        commission=Decimal("5"),
    )

    position = state.positions[0]
    assert position.quantity == 1000
    assert position.remaining_cost_basis == Decimal("10005")
    assert position.last_buy_price == Decimal("10")
    assert position.cumulative_buy_notional == Decimal("10000")


def test_state_tools_reject_unknown_strategy_and_unconfirmed_zero_fill() -> None:
    with pytest.raises(ValueError, match="STRATEGY_UNSUPPORTED"):
        initialize_strategy_state("missing", "1.0.0", Decimal("100000"))

    state = initialize_strategy_state(
        "staged_drawdown_v1",
        "1.0.0",
        Decimal("100000"),
    )
    with pytest.raises(ValueError, match="quantity"):
        apply_confirmed_fill(
            state,
            execution_date=date(2026, 9, 7),
            security_id="600000.SH",
            side=OrderSide.BUY,
            quantity=0,
            execution_price=Decimal("10"),
        )
