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
        account.record_entitlement(
            event_id=action.event_id,
            security_id=action.security_id,
            cash=Decimal(quantity) * action.cash_per_share_after_tax,
            stock_quantity=int(stock),
            split_sellable_quantity=(
                int(split_sellable) if action.action_type == "SPLIT" else 0
            ),
        )

    def apply_ex_date(self, account: SimulatedAccount, action: DividendAction) -> Decimal:
        return account.apply_entitlement_ex_date(
            action.event_id,
            is_split=action.action_type == "SPLIT",
        )

    def apply_pay_date(self, account: SimulatedAccount, action: DividendAction) -> None:
        account.pay_entitlement_cash(action.event_id)

    def apply_stock_list_date(self, account: SimulatedAccount, action: DividendAction) -> None:
        account.release_entitlement_stock(
            action.event_id,
            is_split=action.action_type == "SPLIT",
        )
