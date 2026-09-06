from datetime import date
from decimal import Decimal

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
