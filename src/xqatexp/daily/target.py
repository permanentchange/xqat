from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol, cast

from xqatexp.domain.contracts import AllocationDecision, CustomFactorView, TargetPortfolio
from xqatexp.portfolio.transitions import annotate_transitions
from xqatexp.strategy.decision import stable_decision_id


class DailyStrategy(Protocol):
    def generate_target(
        self,
        research: Any,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
    ) -> TargetPortfolio: ...


class DailyTargetService:
    def run_decision(
        self,
        strategy: DailyStrategy,
        research: Any,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
        *,
        previous: TargetPortfolio | None,
    ) -> AllocationDecision:
        generator = getattr(strategy, "generate_decision", None)
        if callable(generator):
            decision = cast(AllocationDecision, generator(research, custom, parameters))
        else:
            target = strategy.generate_target(research, custom, parameters)
            decision = AllocationDecision(
                stable_decision_id(
                    target.strategy_id,
                    target.strategy_version,
                    target.decision_date,
                    target.effective_from,
                    "ALLOCATION",
                ),
                target,
            )
        return replace(
            decision,
            target=annotate_transitions(decision.target, previous),
        )

    def run(
        self,
        strategy: DailyStrategy,
        research: Any,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
        *,
        previous: TargetPortfolio | None,
    ) -> TargetPortfolio:
        return self.run_decision(
            strategy,
            research,
            custom,
            parameters,
            previous=previous,
        ).target
