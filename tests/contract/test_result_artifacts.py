from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from tests.integration.test_backtest_engine import _Data, _Strategy
from tests.unit.portfolio.test_validation import target
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.backtest.engine import BacktestEngine
from xqatexp.daily.advice import DailyAdviceService
from xqatexp.domain.contracts import AnalysisPeriod, ResolvedRunContext
from xqatexp.domain.enums import OverwritePolicy, RunMode
from xqatexp.portfolio.rebalance import LotRule
from xqatexp.reporting.publisher import ResultArtifactPublisher


def _context(tmp_path: Path) -> ResolvedRunContext:
    return ResolvedRunContext(
        "daily-001",
        RunMode.DAILY_TARGET,
        "weekly_market_guard_rank_v1",
        "1.0.0",
        {"csi300_etf_id": "510300.SH"},
        tmp_path / "research",
        "a" * 64,
        (),
        tmp_path / "result",
        {"price_model": "NEXT_OPEN"},
        datetime(2026, 9, 4, 10, tzinfo=UTC),
        decision_date=target().decision_date,
    )


def test_daily_target_result_has_exact_validated_file_set(tmp_path: Path) -> None:
    context = _context(tmp_path)
    published = ResultArtifactPublisher().publish_daily_target(
        context, target(), (), (), OverwritePolicy.ERROR
    )
    opened = ArtifactReader().open(published.path)
    assert opened.manifest["artifact_type"] == "DAILY_TARGET_RESULT"
    assert set(opened.verified_files) == {
        "resolved_config.json",
        "target_portfolio.json",
        "target_positions.csv",
        "issues.json",
        "report.md",
    }
    assert "600000.SH" in (published.path / "report.md").read_text(encoding="utf-8")
    assert "0.04" in (published.path / "target_positions.csv").read_text(encoding="utf-8")


def test_daily_advice_and_backtest_publish_complete_contracts(tmp_path: Path) -> None:
    publisher = ResultArtifactPublisher()
    daily_context = replace(
        _context(tmp_path),
        mode=RunMode.DAILY_ADVICE,
        output_path=tmp_path / "advice",
    )
    advice = DailyAdviceService().run(
        target=target(),
        account=None,
        reference_prices={"600000.SH": Decimal("10")},
        lot_rules={"600000.SH": LotRule(100, 100)},
        run_started_at=datetime(2026, 9, 4, 10, tzinfo=UTC),
    )
    daily = publisher.publish_daily_advice(daily_context, target(), advice, OverwritePolicy.ERROR)
    assert set(ArtifactReader().open(daily.path).verified_files) == {
        "resolved_config.json",
        "target_portfolio.json",
        "target_positions.csv",
        "trade_advice.json",
        "trade_advice.csv",
        "issues.json",
        "report.md",
    }

    backtest_result = BacktestEngine().run(
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
    backtest_context = replace(
        _context(tmp_path),
        mode=RunMode.BACKTEST,
        decision_date=None,
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 15),
        output_path=tmp_path / "backtest",
    )
    backtest = publisher.publish_backtest(backtest_context, backtest_result, OverwritePolicy.ERROR)
    assert set(ArtifactReader().open(backtest.path).verified_files) == {
        "resolved_config.json",
        "target_history.parquet",
        "portfolio_daily.parquet",
        "trades.parquet",
        "unfilled.parquet",
        "metrics.json",
        "period_metrics.csv",
        "issues.json",
        "report.md",
    }
    metrics = json.loads((backtest.path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["benchmark_cumulative_return"] == 0.07
    assert metrics["excess_return"] is not None
    assert metrics["rolling_20_min_return"] is None
    with (backtest.path / "period_metrics.csv").open(encoding="utf-8", newline="") as stream:
        periods = list(csv.DictReader(stream))
    assert {(row["period_type"], row["period_label"]) for row in periods} == {
        ("FULL", "ALL"),
        ("CALENDAR_YEAR", "2026"),
    }


def test_backtest_publishes_configured_analysis_period(tmp_path: Path) -> None:
    context = replace(
        _context(tmp_path),
        mode=RunMode.BACKTEST,
        decision_date=None,
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 15),
        analysis_periods=(AnalysisPeriod("opening", date(2026, 9, 4), date(2026, 9, 7)),),
    )
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
    output = ResultArtifactPublisher().publish_backtest(context, result, OverwritePolicy.ERROR)
    with (output.path / "period_metrics.csv").open(encoding="utf-8", newline="") as stream:
        periods = list(csv.DictReader(stream))
    custom = next(item for item in periods if item["period_type"] == "USER_DEFINED")
    assert custom["period_label"] == "opening"
    assert custom["valuation_points"] == "2"


def test_repeated_backtest_has_identical_business_files(tmp_path: Path) -> None:
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
    base = replace(
        _context(tmp_path),
        mode=RunMode.BACKTEST,
        decision_date=None,
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 15),
        output_path=tmp_path / "first" / "result",
    )
    first = ResultArtifactPublisher().publish_backtest(base, result, OverwritePolicy.ERROR)
    second_context = replace(
        base,
        run_id="daily-002",
        generated_at=datetime(2026, 9, 5, 10, tzinfo=UTC),
        output_path=tmp_path / "second" / "result",
    )
    second = ResultArtifactPublisher().publish_backtest(
        second_context, result, OverwritePolicy.ERROR
    )
    for name in ArtifactReader().open(first.path).verified_files:
        assert (first.path / name).read_bytes() == (second.path / name).read_bytes(), name
