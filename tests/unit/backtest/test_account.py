from datetime import date
from decimal import Decimal

import pytest

from xqatexp.backtest.account import SimulatedAccount
from xqatexp.domain.contracts import ExecutionFees, ExecutionRecord
from xqatexp.domain.enums import FillStatus, OrderSide


def record(side, quantity, gross, fees):
    return ExecutionRecord(
        date(2026, 9, 4),
        "600000.SH",
        side,
        quantity,
        quantity,
        Decimal("10"),
        gross,
        fees,
        FillStatus.FILLED,
    )


def test_trade_cash_quantity_and_t_plus_one_sellable_conserve_account() -> None:
    account = SimulatedAccount(Decimal("10000"))
    fee = ExecutionFees(Decimal("5"), Decimal("0.10"), Decimal("0"))
    account.apply_trade(
        record(OrderSide.BUY, 100, Decimal("1000"), fee), release_date=date(2026, 9, 5)
    )
    assert account.cash_available == Decimal("8994.90")
    assert account.positions["600000.SH"] == 100
    assert account.sellable_quantities.get("600000.SH", 0) == 0
    account.release_sellable(date(2026, 9, 5))
    assert account.sellable_quantities["600000.SH"] == 100


def test_sell_net_proceeds_are_available_same_day() -> None:
    account = SimulatedAccount(Decimal("1000"), {"600000.SH": 100}, {"600000.SH": 100})
    fee = ExecutionFees(Decimal("5"), Decimal("0.10"), Decimal("0.50"))
    account.apply_trade(record(OrderSide.SELL, 100, Decimal("1000"), fee))
    assert account.cash_available == Decimal("1994.40")
    assert account.positions["600000.SH"] == 0


def test_invalid_cash_trade_and_valuation_states_are_rejected() -> None:
    with pytest.raises(ValueError, match="negative cash"):
        SimulatedAccount(Decimal("-1"))

    account = SimulatedAccount(Decimal("100"))
    with pytest.raises(ValueError, match="invalid buy"):
        account.apply_trade(
            record(OrderSide.BUY, 100, Decimal("1000"), ExecutionFees(0, 0, 0)),
            release_date=date(2026, 9, 5),
        )

    held = SimulatedAccount(Decimal("0"), {"600000.SH": 100}, {"600000.SH": 0})
    with pytest.raises(ValueError, match="invalid sell"):
        held.apply_trade(record(OrderSide.SELL, 100, Decimal("1000"), ExecutionFees(0, 0, 0)))
    with pytest.raises(ValueError, match="BACKTEST_VALUATION_MISSING"):
        held.equity({})

    held.positions["600000.SH"] = -1
    with pytest.raises(ValueError, match="quantity"):
        held._assert_invariants()
