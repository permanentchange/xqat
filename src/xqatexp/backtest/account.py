from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from xqatexp.backtest.account_events import (
    AccountEvent,
    CashDividendDeclared,
    CashDividendPaid,
    CashInitialized,
    DividendEntitlementRecorded,
    SellableReleased,
    SplitApplied,
    StockDistributionApplied,
    TradeFilled,
    ValuationRecorded,
)
from xqatexp.domain.contracts import ExecutionRecord
from xqatexp.domain.enums import OrderSide


class SimulatedAccount:
    def __init__(
        self,
        initial_cash: Decimal,
        positions: Mapping[str, int] | None = None,
        sellable: Mapping[str, int] | None = None,
    ) -> None:
        if initial_cash < 0:
            raise ValueError("BACKTEST_ACCOUNT_CONSERVATION_BROKEN: negative cash")
        self.cash_available = initial_cash
        self.cash_receivable = Decimal("0")
        self.positions = dict(positions or {})
        self.sellable_quantities = dict(sellable or {})
        self.pending_sellable: dict[date, dict[str, int]] = {}
        self.entitlements: dict[str, tuple[str, Decimal, int, int]] = {}
        self.ledger: list[AccountEvent] = [CashInitialized(initial_cash)]
        self._assert_invariants()

    def apply_trade(self, record: ExecutionRecord, *, release_date: date | None = None) -> None:
        security_id = record.security_id
        if record.side is OrderSide.BUY:
            cost = record.gross_amount + record.fees.total
            if cost > self.cash_available or release_date is None:
                raise ValueError("BACKTEST_ACCOUNT_CONSERVATION_BROKEN: invalid buy")
            self.cash_available -= cost
            self.positions[security_id] = (
                self.positions.get(security_id, 0) + record.filled_quantity
            )
            pending = self.pending_sellable.setdefault(release_date, {})
            pending[security_id] = pending.get(security_id, 0) + record.filled_quantity
        else:
            if record.filled_quantity > self.sellable_quantities.get(security_id, 0):
                raise ValueError("BACKTEST_ACCOUNT_CONSERVATION_BROKEN: invalid sell")
            self.positions[security_id] = (
                self.positions.get(security_id, 0) - record.filled_quantity
            )
            self.sellable_quantities[security_id] -= record.filled_quantity
            self.cash_available += record.gross_amount - record.fees.total
        self.ledger.append(TradeFilled(record))
        self._assert_invariants()

    def release_sellable(self, on_date: date) -> None:
        for release_date in sorted(tuple(self.pending_sellable)):
            if release_date <= on_date:
                for security_id, quantity in self.pending_sellable.pop(release_date).items():
                    self.sellable_quantities[security_id] = (
                        self.sellable_quantities.get(security_id, 0) + quantity
                    )
                    self.ledger.append(SellableReleased(security_id, quantity))
        self._assert_invariants()


    def record_entitlement(
        self,
        *,
        event_id: str,
        security_id: str,
        cash: Decimal,
        stock_quantity: int,
        split_sellable_quantity: int,
    ) -> None:
        self.entitlements[event_id] = (
            security_id,
            cash,
            stock_quantity,
            split_sellable_quantity,
        )
        self.ledger.append(
            DividendEntitlementRecorded(
                event_id,
                security_id,
                cash,
                stock_quantity,
                split_sellable_quantity,
            )
        )
        self._assert_invariants()

    def apply_entitlement_ex_date(self, event_id: str, *, is_split: bool) -> Decimal:
        security_id, cash, stock, split_sellable = self.entitlements[event_id]
        self.cash_receivable += cash
        self.positions[security_id] = self.positions.get(security_id, 0) + stock
        if split_sellable:
            self.sellable_quantities[security_id] = (
                self.sellable_quantities.get(security_id, 0) + split_sellable
            )
        self.ledger.append(CashDividendDeclared(event_id, cash))
        if is_split:
            self.ledger.append(SplitApplied(event_id, security_id, stock, split_sellable))
        else:
            self.ledger.append(StockDistributionApplied(event_id, security_id, stock))
        self._assert_invariants()
        return cash

    def pay_entitlement_cash(self, event_id: str) -> None:
        _, cash, _, _ = self.entitlements[event_id]
        self.cash_receivable -= cash
        self.cash_available += cash
        self.ledger.append(CashDividendPaid(event_id, cash))
        self._assert_invariants()

    def release_entitlement_stock(self, event_id: str, *, is_split: bool) -> None:
        if is_split:
            return
        security_id, _, stock, _ = self.entitlements[event_id]
        self.sellable_quantities[security_id] = (
            self.sellable_quantities.get(security_id, 0) + stock
        )
        self.ledger.append(SellableReleased(security_id, stock))
        self._assert_invariants()

    def record_valuation(self, valuation_date: date, nav: Decimal) -> None:
        self.ledger.append(ValuationRecorded(valuation_date, nav))

    def equity(self, prices: Mapping[str, Decimal]) -> Decimal:
        if any(
            security_id not in prices
            for security_id, quantity in self.positions.items()
            if quantity
        ):
            raise ValueError("BACKTEST_VALUATION_MISSING: holding price")
        return (
            self.cash_available
            + self.cash_receivable
            + sum(
                (
                    Decimal(quantity) * prices[security_id]
                    for security_id, quantity in self.positions.items()
                ),
                Decimal("0"),
            )
        )

    def _assert_invariants(self) -> None:
        if self.cash_available < 0 or self.cash_receivable < 0:
            raise ValueError("BACKTEST_ACCOUNT_CONSERVATION_BROKEN: negative cash")
        for security_id, quantity in self.positions.items():
            if quantity < 0 or not 0 <= self.sellable_quantities.get(security_id, 0) <= quantity:
                raise ValueError("BACKTEST_ACCOUNT_CONSERVATION_BROKEN: quantity")
