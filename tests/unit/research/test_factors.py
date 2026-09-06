from __future__ import annotations

import math

import pytest

from xqatexp.research.factors import (
    average_rank_percentiles,
    momentum,
    ols_log_price,
    type7_quantile,
    volatility,
    volume_price_confirmation,
    winsorize_type7,
)


def test_type7_winsorization_and_average_rank_golden_vectors() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert type7_quantile(values, 0.01) == pytest.approx(1.04)
    assert type7_quantile(values, 0.99) == pytest.approx(96.16)
    assert winsorize_type7(values) == pytest.approx([1.04, 2.0, 3.0, 4.0, 96.16])
    assert average_rank_percentiles([1.0, 2.0, 2.0, 4.0]) == [0.0, 0.5, 0.5, 1.0]
    assert average_rank_percentiles([7.0]) == [None]


def test_nonfinite_values_remain_missing_from_cross_section() -> None:
    result = average_rank_percentiles([1.0, float("nan"), 3.0, float("inf")])
    assert result == [0.0, None, 1.0, None]


def test_momentum_and_volatility_require_complete_positive_windows() -> None:
    prices = [100.0 * math.exp(index * 0.01) for index in range(21)]
    assert momentum(prices, 20) == pytest.approx(math.exp(0.20) - 1.0)
    assert volatility(prices, 20) == pytest.approx(0.0, abs=1e-12)
    assert momentum(prices[:-1], 20) is None
    broken = list(prices)
    broken[3] = 0.0
    assert volatility(broken, 20) is None


def test_ols_log_price_gives_positive_r2_and_annualized_slope() -> None:
    prices = [100.0 * math.exp(index * 0.001) for index in range(60)]
    beta, r_squared = ols_log_price(prices)
    assert beta == pytest.approx(0.001)
    assert r_squared == pytest.approx(1.0)


def test_volume_price_confirmation_clips_ratio_and_requires_three_each_side() -> None:
    returns = [0.01, -0.01, 0.02, -0.02, 0.03, -0.03]
    amounts = [1000.0, 100.0, 1000.0, 100.0, 1000.0, 100.0]
    assert volume_price_confirmation(returns, amounts) == pytest.approx(math.log(4.0))
    assert volume_price_confirmation(returns[:5], amounts[:5]) is None
