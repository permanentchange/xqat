from __future__ import annotations

from dataclasses import dataclass

from xqatexp.domain.contracts import CustomFactorView, ResearchDataView, StrategyDeclaration


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
        limitations = (
            ("FACTOR_USER_VISIBILITY_UNVERIFIED",)
            if declaration.required_custom_factors and custom_factors is not None
            else ()
        )
        return ReadinessReport(
            not issues,
            requirements,
            {"trading_days": len(days) / max(1, minimum_days)},
            tuple(sorted(issues)),
            limitations,
        )
