from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from xqatexp.backtest.account import SimulatedAccount


@dataclass(frozen=True, slots=True)
class DividendAction:
    event_id: str
    security_id: str
    record_date: date
    ex_date: date
    pay_date: date
    stock_list_date: date
    cash_per_share_after_tax: Decimal
    stock_ratio: Decimal


class CorporateActionProcessor:
    def record_entitlement(self, account: SimulatedAccount, action: DividendAction) -> None:
        quantity = account.positions.get(action.security_id, 0)
        stock = Decimal(quantity) * action.stock_ratio
        if stock != stock.to_integral_value():
            raise ValueError("BACKTEST_CORPORATE_ACTION_UNSUPPORTED: fractional distribution")
        account.entitlements[action.event_id] = (
            action.security_id,
            Decimal(quantity) * action.cash_per_share_after_tax,
            int(stock),
        )
        account.ledger.append(("DIVIDEND_ENTITLEMENT", action.event_id))

    def apply_ex_date(self, account: SimulatedAccount, action: DividendAction) -> None:
        security_id, cash, stock = account.entitlements[action.event_id]
        account.cash_receivable += cash
        account.positions[security_id] = account.positions.get(security_id, 0) + stock
        account.ledger.append(("CASH_DIVIDEND_DECLARED", cash))
        account.ledger.append(("STOCK_DISTRIBUTION_APPLIED", stock))
        account._assert_invariants()

    def apply_pay_date(self, account: SimulatedAccount, action: DividendAction) -> None:
        _, cash, _ = account.entitlements[action.event_id]
        account.cash_receivable -= cash
        account.cash_available += cash
        account.ledger.append(("CASH_DIVIDEND_PAID", cash))
        account._assert_invariants()

    def apply_stock_list_date(self, account: SimulatedAccount, action: DividendAction) -> None:
        security_id, _, stock = account.entitlements[action.event_id]
        account.sellable_quantities[security_id] = (
            account.sellable_quantities.get(security_id, 0) + stock
        )
        account.ledger.append(("SELLABLE_RELEASED", (security_id, stock)))
        account._assert_invariants()
