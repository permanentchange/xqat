from __future__ import annotations

from dataclasses import dataclass
from typing import cast

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
        coverage = {"trading_days": len(days) / max(1, minimum_days)}
        advanced = getattr(view, "readiness_coverage", None)
        if callable(advanced) and len(days) >= minimum_days:
            measured = cast(dict[str, float], advanced(custom_factors))
            coverage.update(measured)
            if measured.get("market_status", 0.0) < 0.98:
                issues.append("DATA_COVERAGE_INSUFFICIENT")
            if measured.get("financial", 0.0) < 0.90:
                issues.append("DATA_COVERAGE_INSUFFICIENT")
            if measured.get("system_factors", 0.0) < 0.98:
                issues.append("FACTOR_COVERAGE_INSUFFICIENT")
            if measured.get("etf_factors", 0.0) < 1.0:
                issues.append("FACTOR_COVERAGE_INSUFFICIENT")
            if declaration.required_custom_factors and measured.get("custom_factors", 0.0) < 0.98:
                issues.append("FACTOR_COVERAGE_INSUFFICIENT")
        limitations = (
            ("FACTOR_USER_VISIBILITY_UNVERIFIED",)
            if declaration.required_custom_factors and custom_factors is not None
            else ()
        )
        return ReadinessReport(
            not issues,
            requirements,
            coverage,
            tuple(sorted(set(issues))),
            limitations,
        )
