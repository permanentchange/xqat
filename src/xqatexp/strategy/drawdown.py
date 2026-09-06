from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum

from xqatexp.domain.enums import MarketRegime


class OverlayLevel(StrEnum):
    NONE = "NONE"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"


def rolling_drawdown(nav: Sequence[Decimal]) -> tuple[Decimal, int]:
    if not nav or any(value <= 0 for value in nav):
        raise ValueError("STRATEGY_PARAMETER_INVALID: NAV must be positive and nonempty")
    window = tuple(nav[-60:])
    return window[-1] / max(window) - Decimal("1"), len(window)


class DrawdownOverlay:
    def __init__(
        self,
        *,
        caution: Decimal = Decimal("-0.08"),
        defensive: Decimal = Decimal("-0.12"),
        recovery: Decimal = Decimal("-0.05"),
        recovery_weeks: int = 2,
    ) -> None:
        self.level = OverlayLevel.NONE
        self._caution = caution
        self._defensive = defensive
        self._recovery = recovery
        self._recovery_weeks = recovery_weeks
        self._recovery_count = 0

    def update(self, drawdown: Decimal, regime: MarketRegime) -> OverlayLevel:
        if drawdown <= self._defensive:
            self.level = OverlayLevel.DEFENSIVE
            self._recovery_count = 0
            return self.level
        if self.level is OverlayLevel.NONE and drawdown <= self._caution:
            self.level = OverlayLevel.CAUTION
            self._recovery_count = 0
            return self.level
        if self.level is not OverlayLevel.NONE:
            if drawdown >= self._recovery and regime is not MarketRegime.WEAK:
                self._recovery_count += 1
                if self._recovery_count >= self._recovery_weeks:
                    self.level = OverlayLevel.NONE
                    self._recovery_count = 0
            else:
                self._recovery_count = 0
        return self.level
