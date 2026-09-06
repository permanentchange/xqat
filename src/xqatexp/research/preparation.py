from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_RESEARCH_PRICE_QUANTUM = Decimal("0.0000000001")
_RATE_QUANTUM = Decimal("0.000000000001")


def research_price(
    raw_price: Decimal | None,
    adjustment_factor: Decimal | None,
    first_valid_adjustment_factor: Decimal | None,
) -> Decimal | None:
    if (
        raw_price is None
        or adjustment_factor is None
        or first_valid_adjustment_factor is None
        or raw_price <= 0
        or adjustment_factor <= 0
        or first_valid_adjustment_factor <= 0
    ):
        return None
    return (raw_price * adjustment_factor / first_valid_adjustment_factor).quantize(
        _RESEARCH_PRICE_QUANTUM, rounding=ROUND_HALF_UP
    )


def choose_announce_date(actual: str | None, announced: str | None) -> date | None:
    selected = actual or announced
    return (
        date.fromisoformat(f"{selected[:4]}-{selected[4:6]}-{selected[6:8]}") if selected else None
    )


def next_open_date(value: date, open_dates: Sequence[date]) -> date | None:
    return next((candidate for candidate in sorted(open_dates) if candidate > value), None)


def derive_quarter_profit(
    quarter: int, current_ytd: Decimal | None, previous_ytd: Decimal | None
) -> Decimal | None:
    if current_ytd is None or quarter not in {1, 2, 3, 4}:
        return None
    if quarter == 1:
        return current_ytd
    return None if previous_ytd is None else current_ytd - previous_ytd


def derive_ttm_profit(
    quarter: int,
    current_ytd: Decimal | None,
    previous_full_year: Decimal | None,
    previous_same_period: Decimal | None,
) -> Decimal | None:
    if current_ytd is None or quarter not in {1, 2, 3, 4}:
        return None
    if quarter == 4:
        return current_ytd
    if previous_full_year is None or previous_same_period is None:
        return None
    return current_ytd + previous_full_year - previous_same_period


def annualized_roe(roe_yearly_percent: Decimal | None) -> Decimal | None:
    if roe_yearly_percent is None:
        return None
    return (roe_yearly_percent / Decimal("100")).quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP)
