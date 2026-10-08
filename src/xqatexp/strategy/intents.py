from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from xqatexp.domain.contracts import AllocationDecision, StrategyDiagnostics
from xqatexp.domain.enums import OrderSide


@dataclass(frozen=True, slots=True)
class FixedNotional:
    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError("STRATEGY_INTENT_INVALID: fixed notional must be positive")


@dataclass(frozen=True, slots=True)
class InitialCapitalFraction:
    fraction: Decimal

    def __post_init__(self) -> None:
        if not Decimal("0") < self.fraction <= Decimal("1"):
            raise ValueError("STRATEGY_INTENT_INVALID: initial capital fraction must be in (0,1]")


@dataclass(frozen=True, slots=True)
class CurrentPositionFraction:
    fraction: Decimal

    def __post_init__(self) -> None:
        if not Decimal("0") < self.fraction <= Decimal("1"):
            raise ValueError("STRATEGY_INTENT_INVALID: position fraction must be in (0,1]")


@dataclass(frozen=True, slots=True)
class FixedQuantity:
    quantity: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("STRATEGY_INTENT_INVALID: fixed quantity must be a positive integer")


@dataclass(frozen=True, slots=True)
class FullPosition:
    """Size an intent to the full current position."""


type IntentSizing = (
    FixedNotional | InitialCapitalFraction | CurrentPositionFraction | FixedQuantity | FullPosition
)


@dataclass(frozen=True, slots=True)
class TradeIntent:
    intent_id: str
    security_id: str
    side: OrderSide
    sizing: IntentSizing
    reason_codes: tuple[str, ...]
    priority: int = 0


@dataclass(frozen=True, slots=True)
class TradeIntentDecision:
    decision_id: str
    strategy_id: str
    strategy_version: str
    decision_date: date
    effective_from: date
    intents: tuple[TradeIntent, ...]
    diagnostics: StrategyDiagnostics | None = None


type StrategyDecision = AllocationDecision | TradeIntentDecision
