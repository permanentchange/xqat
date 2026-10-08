from __future__ import annotations

import math
import statistics
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.integration.test_backtest_engine import _Data, _Strategy
from xqatexp.artifacts.schemas import SchemaRegistry, SchemaValidationError
from xqatexp.backtest.engine import BacktestEngine, BacktestResult
from xqatexp.reporting.assemblers import BacktestResultAssembler
from xqatexp.reporting.markdown import backtest_markdown

BENCHMARK_FIELDS = (
    "benchmark_annualized_return",
    "benchmark_max_drawdown",
    "benchmark_sharpe",
    "benchmark_calmar",
)


@pytest.fixture
def result() -> BacktestResult:
    original = BacktestEngine().run(
        data=_Data(),
        strategy=_Strategy(),
        parameters={},
        custom=None,
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 15),
        initial_cash=Decimal("10000"),
        execution_assumptions={
            "slippage_bps": Decimal("0"),
            "max_volume_participation": Decimal("0.10"),
        },
    )
    # Identical paths with different scales must produce the same risk metrics.
    values = ("1", "1.1", "0.99", "1.08", "1.12")
    return replace(
        original,
        portfolio_daily=tuple(
            replace(
                original.portfolio_daily[0],
                valuation_date=date(2026, 1, 1) + timedelta(days=index),
                nav=Decimal(value) * 10000,
                benchmark_nav=Decimal(value),
            )
            for index, value in enumerate(values)
        ),
    )


def _metrics(result: BacktestResult) -> dict[str, object]:
    metrics = BacktestResultAssembler(SchemaRegistry()).assemble(result).metrics
    SchemaRegistry().validate_json("metrics", metrics)
    return metrics


def test_benchmark_metrics_match_golden_vector_and_strategy(result: BacktestResult) -> None:
    metrics = _metrics(result)
    returns = (0.1, -0.1, 9 / 99, 4 / 108)
    annualized = 1.12**63 - 1
    sharpe = statistics.mean(returns) / statistics.stdev(returns) * math.sqrt(252)
    assert metrics["benchmark_cumulative_return"] == Decimal("0.12")
    assert metrics["benchmark_annualized_return"] == pytest.approx(annualized)
    assert metrics["benchmark_max_drawdown"] == pytest.approx(-0.1)
    assert metrics["benchmark_sharpe"] == pytest.approx(sharpe)
    assert metrics["benchmark_calmar"] == pytest.approx(annualized / 0.1)
    for key in BENCHMARK_FIELDS:
        assert metrics[key] == metrics[key.removeprefix("benchmark_")]
    assert metrics["max_drawdown_recovery_date"] == "2026-01-05"


@pytest.mark.parametrize("missing_indices", [(0,), (2,), (4,), (0, 1, 2, 3, 4)])
def test_missing_benchmark_never_uses_partial_history(
    result: BacktestResult, missing_indices: tuple[int, ...]
) -> None:
    result = replace(
        result,
        portfolio_daily=tuple(
            replace(item, benchmark_nav=None) if index in missing_indices else item
            for index, item in enumerate(result.portfolio_daily)
        ),
    )
    metrics = _metrics(result)
    for key in (*BENCHMARK_FIELDS, "benchmark_cumulative_return", "excess_return"):
        assert metrics[key] is None
    assert "BENCHMARK_UNAVAILABLE" in metrics["limitations"]
    assert metrics["max_drawdown"] == pytest.approx(-0.1)
    report = backtest_markdown(metrics, 0)
    assert "| 最大回撤 | -0.1 | 不可计算 |" in report


@pytest.mark.parametrize(
    ("values", "expected_sharpe", "expected_calmar"),
    [
        (("1", "1", "1"), False, False),
        (("1", "1.01", "1.03"), True, False),
        (("1", "1.01"), False, False),
        (("1", "0.9"), False, True),
    ],
)
def test_benchmark_degenerate_samples(
    result: BacktestResult,
    values: tuple[str, ...],
    expected_sharpe: bool,
    expected_calmar: bool,
) -> None:
    result = replace(
        result,
        portfolio_daily=tuple(
            replace(item, benchmark_nav=Decimal(value))
            for item, value in zip(result.portfolio_daily, values, strict=False)
        ),
    )
    metrics = _metrics(result)
    assert (metrics["benchmark_sharpe"] is not None) == expected_sharpe
    assert (metrics["benchmark_calmar"] is not None) == expected_calmar
    if expected_calmar:
        assert metrics["benchmark_calmar"] == pytest.approx((0.9**252 - 1) / 0.1)
    else:
        assert metrics["benchmark_max_drawdown"] == 0
    assert "SHORT_PERFORMANCE_SAMPLE" in metrics["limitations"]
    assert "BENCHMARK_UNAVAILABLE" not in metrics["limitations"]


def test_metrics_schema_accepts_legacy_result_and_renderer_handles_absent_fields(
    result: BacktestResult,
) -> None:
    metrics = _metrics(result)
    for key in BENCHMARK_FIELDS:
        del metrics[key]
    SchemaRegistry().validate_json("metrics", metrics)
    report = backtest_markdown(metrics, 0)
    assert "| 最大回撤 | -0.1 | 不可计算 |" in report
    assert "| 累计收益 | 0.12 | 0.12 |" in report


@pytest.mark.parametrize(
    ("field", "value"),
    [("benchmark_max_drawdown", 0.1), ("benchmark_sharpe", "invalid")],
)
def test_metrics_schema_rejects_invalid_benchmark_values(
    result: BacktestResult, field: str, value: object
) -> None:
    metrics = _metrics(result)
    metrics[field] = value
    with pytest.raises(SchemaValidationError):
        SchemaRegistry().validate_json("metrics", metrics)
