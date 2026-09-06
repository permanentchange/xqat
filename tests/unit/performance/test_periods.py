from datetime import date
from decimal import Decimal

from xqatexp.performance.periods import calendar_year_periods


def test_calendar_year_periods_use_only_available_boundaries() -> None:
    points = (
        (date(2025, 12, 31), Decimal("100")),
        (date(2026, 1, 2), Decimal("101")),
        (date(2026, 12, 31), Decimal("110")),
    )
    periods = calendar_year_periods(points)
    assert [item.label for item in periods] == ["2025", "2026"]
    assert periods[0].points == points[:1]
    assert periods[1].points == points[1:]
