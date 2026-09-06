from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PerformanceResult:
    valuation_points: int
    return_intervals: int
    daily_returns: tuple[float, ...]
    cumulative_return: float
    annualized_return: float
    annualized_volatility: float | None
    max_drawdown: float
    max_drawdown_peak_date: date
    max_drawdown_trough_date: date
    max_drawdown_recovery_date: date | None
    sharpe: float | None
    calmar: float | None
    limitations: tuple[str, ...]


class PerformanceAnalyzer:
    def analyze(
        self,
        points: Sequence[tuple[date, Decimal]],
        *,
        risk_free_rate: Decimal,
    ) -> PerformanceResult:
        if len(points) < 2 or risk_free_rate <= -1:
            raise ValueError("PERFORMANCE_INSUFFICIENT_SAMPLE: at least two NAV points")
        if any(nav <= 0 for _, nav in points):
            raise ValueError("BACKTEST_VALUATION_MISSING: NAV must be positive")
        returns = tuple(
            float(points[index][1] / points[index - 1][1] - 1) for index in range(1, len(points))
        )
        intervals = len(returns)
        cumulative = float(points[-1][1] / points[0][1] - 1)
        annualized = float((points[-1][1] / points[0][1]) ** (Decimal(252) / intervals) - 1)
        volatility = statistics.stdev(returns) * math.sqrt(252) if intervals >= 2 else None
        rf_daily = float((Decimal("1") + risk_free_rate) ** (Decimal("1") / 252) - 1)
        excess = tuple(value - rf_daily for value in returns)
        excess_std = statistics.stdev(excess) if len(excess) >= 2 else 0.0
        sharpe = statistics.mean(excess) / excess_std * math.sqrt(252) if excess_std > 0 else None
        peak_nav = points[0][1]
        peak_date = points[0][0]
        worst = Decimal("0")
        worst_peak = peak_date
        trough = peak_date
        peak_value_at_worst = peak_nav
        for point_date, nav in points:
            if nav > peak_nav:
                peak_nav, peak_date = nav, point_date
            drawdown = nav / peak_nav - 1
            if drawdown < worst:
                worst = drawdown
                worst_peak = peak_date
                peak_value_at_worst = peak_nav
                trough = point_date
        recovery = next(
            (
                point_date
                for point_date, nav in points
                if point_date > trough and nav >= peak_value_at_worst
            ),
            None,
        )
        calmar = annualized / abs(float(worst)) if worst < 0 else None
        limitations = ("PERFORMANCE_INSUFFICIENT_SAMPLE",) if intervals < 60 else ()
        return PerformanceResult(
            len(points),
            intervals,
            returns,
            cumulative,
            annualized,
            volatility,
            float(worst),
            worst_peak,
            trough,
            recovery,
            sharpe,
            calmar,
            limitations,
        )
