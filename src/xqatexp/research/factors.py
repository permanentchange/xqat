from __future__ import annotations

import math
from collections.abc import Sequence


def _finite(value: float) -> bool:
    return math.isfinite(value)


def type7_quantile(values: Sequence[float], quantile: float) -> float:
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(value for value in values if _finite(value))
    if not ordered:
        raise ValueError("at least one finite value is required")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return float(ordered[lower] + fraction * (ordered[upper] - ordered[lower]))


def winsorize_type7(
    values: Sequence[float], *, lower: float = 0.01, upper: float = 0.99
) -> list[float]:
    lower_bound = type7_quantile(values, lower)
    upper_bound = type7_quantile(values, upper)
    return [min(max(value, lower_bound), upper_bound) for value in values]


def average_rank_percentiles(values: Sequence[float]) -> list[float | None]:
    finite = sorted((value, index) for index, value in enumerate(values) if _finite(value))
    result: list[float | None] = [None] * len(values)
    count = len(finite)
    if count <= 1:
        return result
    cursor = 0
    while cursor < count:
        end = cursor + 1
        while end < count and finite[end][0] == finite[cursor][0]:
            end += 1
        average_rank = ((cursor + 1) + end) / 2.0
        percentile = (average_rank - 1.0) / (count - 1)
        for _, original_index in finite[cursor:end]:
            result[original_index] = percentile
        cursor = end
    return result


def _valid_prices(prices: Sequence[float], expected: int) -> bool:
    return len(prices) == expected and all(_finite(value) and value > 0.0 for value in prices)


def momentum(prices: Sequence[float], lookback: int) -> float | None:
    if lookback < 1 or not _valid_prices(prices, lookback + 1):
        return None
    return prices[-1] / prices[0] - 1.0


def volatility(prices: Sequence[float], return_count: int) -> float | None:
    if return_count < 2 or not _valid_prices(prices, return_count + 1):
        return None
    returns = [math.log(prices[index] / prices[index - 1]) for index in range(1, len(prices))]
    mean = math.fsum(returns) / return_count
    variance = math.fsum((value - mean) ** 2 for value in returns) / (return_count - 1)
    value = math.sqrt(variance) * math.sqrt(252.0)
    return 0.0 if value == 0.0 else value


def ols_log_price(prices: Sequence[float]) -> tuple[float | None, float | None]:
    count = len(prices)
    if count < 2 or not _valid_prices(prices, count):
        return None, None
    x_mean = (count - 1) / 2.0
    y = [math.log(value) for value in prices]
    y_mean = math.fsum(y) / count
    x_variance = math.fsum((index - x_mean) ** 2 for index in range(count))
    y_variance = math.fsum((value - y_mean) ** 2 for value in y)
    if x_variance == 0.0 or y_variance == 0.0:
        return None, None
    covariance = math.fsum((index - x_mean) * (value - y_mean) for index, value in enumerate(y))
    beta = covariance / x_variance
    residual = math.fsum(
        (value - (y_mean + beta * (index - x_mean))) ** 2 for index, value in enumerate(y)
    )
    r_squared = 1.0 - residual / y_variance
    return beta, max(0.0, min(1.0, r_squared))


def trend_stability(prices: Sequence[float]) -> float | None:
    beta, r_squared = ols_log_price(prices)
    if beta is None or r_squared is None:
        return None
    return 0.0 if beta <= 0.0 else r_squared


def annualized_log_slope(prices: Sequence[float]) -> float | None:
    beta, _ = ols_log_price(prices)
    if beta is None:
        return None
    return math.exp(beta * 252.0) - 1.0


def volume_price_confirmation(returns: Sequence[float], amounts: Sequence[float]) -> float | None:
    if len(returns) != len(amounts):
        raise ValueError("returns and amounts must have equal length")
    pairs = [
        (daily_return, amount)
        for daily_return, amount in zip(returns, amounts, strict=True)
        if _finite(daily_return) and _finite(amount) and amount >= 0.0
    ]
    up = [amount for daily_return, amount in pairs if daily_return > 0.0]
    down = [amount for daily_return, amount in pairs if daily_return < 0.0]
    if len(up) < 3 or len(down) < 3:
        return None
    down_mean = math.fsum(down) / len(down)
    if down_mean <= 0.0:
        return None
    ratio = (math.fsum(up) / len(up)) / down_mean
    return math.log(min(max(ratio, 0.25), 4.0))
