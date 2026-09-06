from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from xqatexp.domain.contracts import CustomFactorView, TargetPortfolio
from xqatexp.portfolio.transitions import annotate_transitions


class DailyStrategy(Protocol):
    def generate_target(
        self,
        research: Any,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
    ) -> TargetPortfolio: ...


class DailyTargetService:
    def run(
        self,
        strategy: DailyStrategy,
        research: Any,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
        *,
        previous: TargetPortfolio | None,
    ) -> TargetPortfolio:
        return annotate_transitions(
            strategy.generate_target(research, custom, parameters), previous
        )
