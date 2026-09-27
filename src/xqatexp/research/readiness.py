from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from xqatexp.domain.contracts import (
    CustomFactorView,
    DataRequirement,
    ResearchDataView,
    StrategyDeclaration,
)


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    is_ready: bool
    requirements: tuple[str, ...]
    coverage: dict[str, float]
    issues: tuple[str, ...]
    limitations: tuple[str, ...]


class ReadinessChecker:
    def check(
        self,
        declaration: StrategyDeclaration,
        view: ResearchDataView,
        custom_factors: CustomFactorView | None = None,
    ) -> ReadinessReport:
        if declaration.data_requirements:
            return self._check_declared_requirements(declaration, view, custom_factors)
        return self._check_legacy(declaration, view, custom_factors)

    def _check_declared_requirements(
        self,
        declaration: StrategyDeclaration,
        view: ResearchDataView,
        custom_factors: CustomFactorView | None,
    ) -> ReadinessReport:
        requirements = tuple(item.requirement_id for item in declaration.data_requirements)
        coverage: dict[str, float] = {}
        issues: list[str] = []
        evaluator = getattr(view, "requirement_coverage", None)
        days = tuple(view.trading_days(view.earliest_date, view.decision_date))

        for requirement in declaration.data_requirements:
            value = self._coverage_for(
                requirement,
                days_count=len(days),
                evaluator=evaluator,
                custom_factors=custom_factors,
            )
            coverage[requirement.requirement_id] = value
            if requirement.required and value < requirement.minimum_coverage:
                issues.append(requirement.failure_code)

        limitations = self._limitations(declaration, custom_factors)
        return ReadinessReport(
            not issues,
            requirements,
            coverage,
            tuple(sorted(set(issues))),
            limitations,
        )

    @staticmethod
    def _coverage_for(
        requirement: DataRequirement,
        *,
        days_count: int,
        evaluator: object,
        custom_factors: CustomFactorView | None,
    ) -> float:
        if requirement.dataset == "TRADING_DAYS":
            return min(days_count / max(1, requirement.lookback_trade_days), 1.0)
        if requirement.dataset == "CUSTOM_FACTORS" and custom_factors is None:
            return 0.0
        if not callable(evaluator):
            return 1.0
        measured = cast(
            float,
            evaluator(requirement, custom_factors),
        )
        return measured

    def _check_legacy(
        self,
        declaration: StrategyDeclaration,
        view: ResearchDataView,
        custom_factors: CustomFactorView | None,
    ) -> ReadinessReport:
        days = tuple(view.trading_days(view.earliest_date, view.decision_date))
        requirements = tuple(
            sorted(
                {
                    *declaration.required_fields,
                    *declaration.required_system_factors,
                    *declaration.required_custom_factors,
                }
            )
        )
        issues = []
        minimum_days = (
            313 if declaration.lookback_trade_days >= 313 else declaration.lookback_trade_days
        )
        if len(days) < minimum_days:
            issues.append("STRATEGY_WARMUP_INSUFFICIENT")
        if declaration.required_custom_factors and custom_factors is None:
            issues.append("FACTOR_COVERAGE_INSUFFICIENT")
        coverage = {"trading_days": len(days) / max(1, minimum_days)}
        limitations = self._limitations(declaration, custom_factors)
        return ReadinessReport(
            not issues,
            requirements,
            coverage,
            tuple(sorted(set(issues))),
            limitations,
        )

    @staticmethod
    def _limitations(
        declaration: StrategyDeclaration,
        custom_factors: CustomFactorView | None,
    ) -> tuple[str, ...]:
        return (
            ("FACTOR_USER_VISIBILITY_UNVERIFIED",)
            if declaration.required_custom_factors and custom_factors is not None
            else ()
        )
