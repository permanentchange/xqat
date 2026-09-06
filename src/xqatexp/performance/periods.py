from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Period:
    label: str
    points: tuple[tuple[date, Decimal], ...]


def calendar_year_periods(points: Sequence[tuple[date, Decimal]]) -> tuple[Period, ...]:
    years = sorted({point_date.year for point_date, _ in points})
    return tuple(
        Period(str(year), tuple(item for item in points if item[0].year == year)) for year in years
    )
