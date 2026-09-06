import math
from datetime import date, timedelta
from decimal import Decimal

import pytest

from xqatexp.performance.metrics import PerformanceAnalyzer


def test_five_point_nav_golden_vector() -> None:
    start = date(2026, 1, 1)
    points = tuple(
        (start + timedelta(days=index), Decimal(str(value)))
        for index, value in enumerate((100, 110, 99, 108, 108))
    )
    result = PerformanceAnalyzer().analyze(points, risk_free_rate=Decimal("0"))
    assert result.valuation_points == 5
    assert result.return_intervals == 4
    assert result.daily_returns == pytest.approx((0.1, -0.1, 9 / 99, 0.0))
    assert result.cumulative_return == pytest.approx(0.08)
    assert result.annualized_return == pytest.approx(1.08**63 - 1)
    expected_vol = __import__("statistics").stdev((0.1, -0.1, 9 / 99, 0.0)) * math.sqrt(252)
    assert result.annualized_volatility == pytest.approx(expected_vol)
    assert result.max_drawdown == pytest.approx(-0.1)
    assert result.max_drawdown_peak_date == start + timedelta(days=1)
    assert result.max_drawdown_trough_date == start + timedelta(days=2)
    assert result.max_drawdown_recovery_date is None


def test_one_interval_has_return_but_no_volatility_or_sharpe() -> None:
    result = PerformanceAnalyzer().analyze(
        ((date(2026, 1, 1), Decimal("100")), (date(2026, 1, 2), Decimal("101"))),
        risk_free_rate=Decimal("0"),
    )
    assert result.cumulative_return == pytest.approx(0.01)
    assert result.annualized_volatility is None
    assert result.sharpe is None
    assert result.limitations == ("SHORT_PERFORMANCE_SAMPLE",)
