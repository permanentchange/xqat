from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tests.contract.test_result_artifacts import _context
from tests.integration.test_backtest_engine import _Data, _Strategy
from tests.unit.portfolio.test_validation import target
from xqatexp.backtest.engine import BacktestEngine
from xqatexp.cli import main
from xqatexp.domain.enums import OverwritePolicy, RunMode
from xqatexp.performance.metrics import PerformanceAnalyzer
from xqatexp.reporting.publisher import ResultArtifactPublisher


def test_result_show_uses_published_facts_without_recalculation(tmp_path, capsys) -> None:
    context = _context(tmp_path)
    ResultArtifactPublisher().publish_daily_target(context, target(), (), (), OverwritePolicy.ERROR)
    assert main(["result", "show", "--input", str(context.output_path)]) == 0
    output = capsys.readouterr().out
    assert "DAILY_TARGET_RESULT" in output
    assert "600000.SH" in output


@pytest.mark.parametrize("output_format", ["summary", "markdown"])
def test_backtest_show_displays_published_comparison_without_recalculation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    result = BacktestEngine().run(
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
    context = replace(
        _context(tmp_path),
        mode=RunMode.BACKTEST,
        decision_date=None,
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 15),
    )
    output = ResultArtifactPublisher().publish_backtest(context, result, OverwritePolicy.ERROR)
    report = (output.path / "report.md").read_text(encoding="utf-8")
    metrics = json.loads((output.path / "metrics.json").read_text(encoding="utf-8"))
    assert "| 指标 | 策略 | 沪深300基准 |" in report
    for label, key in (
        ("累计收益", "cumulative_return"),
        ("年化收益", "annualized_return"),
        ("最大回撤", "max_drawdown"),
        ("夏普率", "sharpe"),
        ("Calmar (年化收益 / 最大回撤绝对值)", "calmar"),
    ):
        strategy = "不可计算" if metrics[key] is None else str(metrics[key])
        benchmark = metrics[f"benchmark_{key}"]
        benchmark = "不可计算" if benchmark is None else str(benchmark)
        assert f"| {label} | {strategy} | {benchmark} |" in report

    def unexpected_analysis(*args: object, **kwargs: object) -> None:
        pytest.fail("result show must not recalculate metrics")

    monkeypatch.setattr(PerformanceAnalyzer, "analyze", unexpected_analysis)
    monkeypatch.setattr(BacktestEngine, "run", unexpected_analysis)
    assert main(
        ["result", "show", "--input", str(output.path), "--format", output_format]
    ) == 0
    shown = capsys.readouterr().out
    if output_format == "markdown":
        assert shown == report
    else:
        assert shown.endswith(report)
