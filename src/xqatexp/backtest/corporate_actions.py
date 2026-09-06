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
    action_type: str = "STOCK_DISTRIBUTION"
    split_ratio: Decimal = Decimal("0")


class CorporateActionProcessor:
    def record_entitlement(self, account: SimulatedAccount, action: DividendAction) -> None:
        quantity = account.positions.get(action.security_id, 0)
        if quantity and action.action_type not in {
            "CASH_DIVIDEND",
            "STOCK_DISTRIBUTION",
            "SPLIT",
        }:
            raise ValueError(
                "BACKTEST_CORPORATE_ACTION_UNSUPPORTED: "
                f"{action.action_type} {action.security_id} {action.record_date}"
            )
        ratio = action.split_ratio if action.action_type == "SPLIT" else action.stock_ratio
        stock = Decimal(quantity) * ratio
        if stock != stock.to_integral_value():
            raise ValueError("BACKTEST_CORPORATE_ACTION_UNSUPPORTED: fractional distribution")
        split_sellable = Decimal(account.sellable_quantities.get(action.security_id, 0)) * ratio
        if split_sellable != split_sellable.to_integral_value():
            raise ValueError("BACKTEST_CORPORATE_ACTION_UNSUPPORTED: fractional sellable split")
        account.entitlements[action.event_id] = (
            action.security_id,
            Decimal(quantity) * action.cash_per_share_after_tax,
            int(stock),
            int(split_sellable) if action.action_type == "SPLIT" else 0,
        )
        account.ledger.append(("DIVIDEND_ENTITLEMENT", action.event_id))

    def apply_ex_date(self, account: SimulatedAccount, action: DividendAction) -> Decimal:
        security_id, cash, stock, split_sellable = account.entitlements[action.event_id]
        account.cash_receivable += cash
        account.positions[security_id] = account.positions.get(security_id, 0) + stock
        if split_sellable:
            account.sellable_quantities[security_id] = (
                account.sellable_quantities.get(security_id, 0) + split_sellable
            )
        account.ledger.append(("CASH_DIVIDEND_DECLARED", cash))
        account.ledger.append(
            (
                "SPLIT_APPLIED" if action.action_type == "SPLIT" else "STOCK_DISTRIBUTION_APPLIED",
                stock,
            )
        )
        account._assert_invariants()
        return cash

    def apply_pay_date(self, account: SimulatedAccount, action: DividendAction) -> None:
        _, cash, _, _ = account.entitlements[action.event_id]
        account.cash_receivable -= cash
        account.cash_available += cash
        account.ledger.append(("CASH_DIVIDEND_PAID", cash))
        account._assert_invariants()

    def apply_stock_list_date(self, account: SimulatedAccount, action: DividendAction) -> None:
        security_id, _, stock, _ = account.entitlements[action.event_id]
        if action.action_type == "SPLIT":
            return
        account.sellable_quantities[security_id] = (
            account.sellable_quantities.get(security_id, 0) + stock
        )
        account.ledger.append(("SELLABLE_RELEASED", (security_id, stock)))
        account._assert_invariants()
