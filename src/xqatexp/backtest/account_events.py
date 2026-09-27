from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from xqatexp.domain.contracts import ExecutionRecord


@dataclass(frozen=True, slots=True)
class CashInitialized:
    amount: Decimal


@dataclass(frozen=True, slots=True)
class TradeFilled:
    record: ExecutionRecord


@dataclass(frozen=True, slots=True)
class SellableReleased:
    security_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class DividendEntitlementRecorded:
    event_id: str
    security_id: str
    cash: Decimal
    stock_quantity: int
    split_sellable_quantity: int


@dataclass(frozen=True, slots=True)
class CashDividendDeclared:
    event_id: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CashDividendPaid:
    event_id: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class StockDistributionApplied:
    event_id: str
    security_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class SplitApplied:
    event_id: str
    security_id: str
    quantity: int
    sellable_quantity: int


@dataclass(frozen=True, slots=True)
class ValuationRecorded:
    valuation_date: date
    nav: Decimal


AccountEvent = (
    CashInitialized
    | TradeFilled
    | SellableReleased
    | DividendEntitlementRecorded
    | CashDividendDeclared
    | CashDividendPaid
    | StockDistributionApplied
    | SplitApplied
    | ValuationRecorded
)
