from __future__ import annotations

from datetime import date, timedelta

from xqatexp.domain.contracts import StrategyDeclaration
from xqatexp.research.readiness import ReadinessChecker


class _View:
    def __init__(self, count: int) -> None:
        self.earliest_date = date(2025, 1, 1)
        self.decision_date = self.earliest_date + timedelta(days=count - 1)
        self._days = tuple(self.earliest_date + timedelta(days=index) for index in range(count))

    def trading_days(self, start, end):
        return tuple(day for day in self._days if start <= day <= end)

    def slice(self, as_of_date):
        raise AssertionError("readiness must not request strategy facts")


def _declaration(*, custom: bool = False) -> StrategyDeclaration:
    return StrategyDeclaration(
        "weekly_market_guard_rank_v1",
        "1.0.0",
        "1.0",
        "WEEKLY",
        320,
        ("research_close",),
        ("momentum_40_v1",),
        ("quality_score",) if custom else (),
        {},
    )


def test_readiness_requires_313_days_for_320_day_strategy_view() -> None:
    report = ReadinessChecker().check(_declaration(), _View(312))
    assert report.is_ready is False
    assert report.issues == ("STRATEGY_WARMUP_INSUFFICIENT",)
    assert ReadinessChecker().check(_declaration(), _View(313)).is_ready is True


def test_required_custom_factor_cannot_be_silently_omitted() -> None:
    report = ReadinessChecker().check(_declaration(custom=True), _View(313))
    assert report.is_ready is False
    assert "FACTOR_COVERAGE_INSUFFICIENT" in report.issues
