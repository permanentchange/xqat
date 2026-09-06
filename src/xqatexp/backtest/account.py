from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

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
        self.ledger: list[tuple[str, object]] = [("CASH_INITIALIZED", initial_cash)]
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
        self.ledger.append(("TRADE_FILLED", record))
        self._assert_invariants()

    def release_sellable(self, on_date: date) -> None:
        for release_date in sorted(tuple(self.pending_sellable)):
            if release_date <= on_date:
                for security_id, quantity in self.pending_sellable.pop(release_date).items():
                    self.sellable_quantities[security_id] = (
                        self.sellable_quantities.get(security_id, 0) + quantity
                    )
                    self.ledger.append(("SELLABLE_RELEASED", (security_id, quantity)))
        self._assert_invariants()

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
