from datetime import date
from decimal import Decimal

from xqatexp.backtest.account import SimulatedAccount
from xqatexp.backtest.corporate_actions import CorporateActionProcessor, DividendAction


def test_cash_and_stock_distribution_golden_event_order() -> None:
    account = SimulatedAccount(Decimal("0"), {"600000.SH": 1000}, {"600000.SH": 1000})
    action = DividendAction(
        "event",
        "600000.SH",
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
        Decimal("0.10"),
        Decimal("0.20"),
    )
    processor = CorporateActionProcessor()
    processor.record_entitlement(account, action)
    processor.apply_ex_date(account, action)
    assert account.cash_receivable == Decimal("100")
    assert account.positions["600000.SH"] == 1200
    assert account.sellable_quantities["600000.SH"] == 1000
    processor.apply_pay_date(account, action)
    processor.apply_stock_list_date(account, action)
    assert account.cash_available == Decimal("100")
    assert account.cash_receivable == 0
    assert account.sellable_quantities["600000.SH"] == 1200
