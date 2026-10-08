import csv
import hashlib
import json
import tomllib
from datetime import UTC, date, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xqatexp.application.optimization import (
    collect_files,
    merge_optimizations,
    reselect_optimization,
    result_from_value,
    run_optimization,
)
from xqatexp.application.workflows import StrategyWorkflowService
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.cli import main
from xqatexp.config import resolve_config
from xqatexp.demo import create_offline_research
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.optimization.models import digest
from xqatexp.optimization.storage import read_json, write_json


@pytest.fixture
def inputs(tmp_path):
    research = tmp_path / "research"
    create_offline_research(research)
    # A decline followed by a rebound and rally produces real buys and tiered sells.
    market_path = research / "tables/market_daily.parquet"
    table = pq.read_table(market_path)
    rows = table.to_pylist()
    prices = {
        date(2026, 8, 31): 100,
        date(2026, 9, 1): 94,
        date(2026, 9, 2): 95,
        date(2026, 9, 3): 110,
        date(2026, 9, 4): 120,
        date(2026, 9, 7): 125,
    }
    from decimal import Decimal

    for row in rows:
        if row["security_id"] == "510300.SH" and row["trade_date"] in prices:
            close = Decimal(prices[row["trade_date"]])
            for field in ("open_raw", "close_raw", "research_open", "research_close"):
                row[field] = close
            for prefix in ("", "research_"):
                row[f"{prefix}high" if prefix else "high_raw"] = close + 1
                row[f"{prefix}low" if prefix else "low_raw"] = close - 1
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), market_path)
    # Refresh the fixture's manifest as if the data had been published by its builder.
    manifest = json.loads((research / "manifest.json").read_text())
    for item in manifest["files"]:
        if item["path"] == "tables/market_daily.parquet":
            item["sha256"] = hashlib.sha256(market_path.read_bytes()).hexdigest()
            item["size_bytes"] = market_path.stat().st_size
    (research / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    config = tmp_path / "strategy.toml"
    config.write_text(f'''schema_version = "1.0"
mode = "BACKTEST"
strategy_id = "staged_drawdown_v1"
research_artifact = "{research.as_posix()}"
start_date = "2026-09-03"
end_date = "2026-09-08"
output = "{(tmp_path / "ordinary-result").as_posix()}"
[strategy]
security_id = "510300.SH"
lookback_trade_days = 2
minimum_down_days = 1
cumulative_decline_threshold = 0.05
single_day_crash_threshold = 0.20
entry_confirmation_mode = "none"
take_profit_mode = "tiered"
take_profit_levels = [0.10, 0.20, 0.30]
take_profit_sell_fractions = [0.30, 0.30, 0.40]
buy_fraction = 0.20
[execution]
slippage_bps = 20
''')
    opt = tmp_path / "opt.toml"
    opt.write_text("""schema_version = "1.0"
method = "grid"
start_date = "2026-09-01"
end_date = "2026-09-07"
workers = 1
[objective]
metric = "sharpe"
direction = "maximize"
[fixed_strategy]
entry_confirmation_mode = "ma_rebound"
[parameters]
entry_confirmation_ma_days = [2, 3]
entry_confirmation_window_days = [1, 2]
""")
    return config, opt


def run(inputs, output, **kwargs):
    return run_optimization(
        config=inputs[0], opt_config=inputs[1], output=output, progress=lambda _: None, **kwargs
    )


def metrics(output):
    return {key: value["metrics"] for key, value in read_json(output / "checkpoint.json").items()}


def test_cli_dry_run_is_read_only_and_dates_override_without_changing_config(
    inputs, tmp_path, capsys
):
    config, opt = inputs
    before = config.read_bytes()
    output = tmp_path / "new-parent" / "dry"
    assert (
        main(
            [
                "backtest",
                "opt",
                "--config",
                str(config),
                "--opt-config",
                str(opt),
                "--output",
                str(output),
                "--start-date",
                "2026-09-02",
                "--dry-run",
            ]
        )
        == 0
    )
    assert "start=2026-09-02 end=2026-09-07 candidates=4" in capsys.readouterr().out
    assert not output.parent.exists()
    assert config.read_bytes() == before
    assert (
        main(
            [
                "backtest",
                "opt",
                "--config",
                str(config),
                "--opt-config",
                str(opt),
                "--output",
                str(opt.parent),
                "--dry-run",
            ]
        )
        == 2
    )


def test_parallel_matches_serial_and_best_config_reproduces_real_fills(inputs, tmp_path):
    before = inputs[0].read_bytes()
    serial, parallel = tmp_path / "serial", tmp_path / "parallel"
    a = run(inputs, serial, workers=1)
    b = run(inputs, parallel, workers=2)
    assert a["status"] == b["status"] == "complete"
    assert metrics(serial) == metrics(parallel)
    assert any(value["trade_count"] >= 1 for value in metrics(serial).values())
    for item in read_json(serial / "selected.json"):
        ArtifactReader().open(serial / "trials" / item["id"])
    best = read_json(serial / "best.json")
    config = tomllib.loads((serial / "best-config.toml").read_text())
    assert config["start_date"] == "2026-09-01"
    assert config["end_date"] == "2026-09-07"
    assert config["strategy"]["buy_fraction"] == 0.20
    assert config["execution"]["slippage_bps"] == 20
    context = resolve_config(
        {"output": tmp_path / "reproduced"},
        serial / "best-config.toml",
        run_id="reproduce",
        generated_at=datetime.now(UTC),
    )
    StrategyWorkflowService().backtest(context, OverwritePolicy.ERROR)
    original = dict(best["metrics"])
    original.pop("trade_count")
    assert read_json(tmp_path / "reproduced/metrics.json") == original
    assert inputs[0].read_bytes() == before
    initial_checkpoint = (serial / "checkpoint.json").read_bytes()
    assert run(inputs, serial, resume=True, workers=2)["status"] == "complete"
    assert (serial / "checkpoint.json").read_bytes() == initial_checkpoint
    assert run(inputs, serial, existing="skip")["status"] == "complete"
    with pytest.raises(Exception, match="OPT_OUTPUT_EXISTS"):
        run(inputs, serial)
    assert run(inputs, serial, existing="overwrite")["status"] == "complete"
    assert not list(tmp_path.glob(".serial.backup-*"))


def test_interrupt_resume_recovers_published_checkpoint_gap_and_rejects_changed_inputs(
    inputs, tmp_path, monkeypatch
):
    output = tmp_path / "interrupted"
    original = StrategyWorkflowService.backtest
    calls = 0

    def interrupt(self, *args):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise KeyboardInterrupt
        return original(self, *args)

    monkeypatch.setattr(StrategyWorkflowService, "backtest", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(inputs, output)
    assert read_json(output / "study_manifest.json")["status"] == "interrupted"
    assert len(read_json(output / "checkpoint.json")) == 1
    monkeypatch.setattr(StrategyWorkflowService, "backtest", original)
    # Simulate publication finishing before the parent recorded its result.
    write_json(output / "checkpoint.json", {})
    manifest = read_json(output / "study_manifest.json")
    manifest["files"].pop("checkpoint.json")
    write_json(output / "study_manifest.json", manifest)
    assert run(inputs, output, resume=True)["status"] == "complete"
    inputs[0].write_text(inputs[0].read_text() + "\n")
    with pytest.raises(ValueError, match="inputs changed"):
        run(inputs, output, resume=True)


def test_failed_trials_are_retained_and_retried_on_resume(inputs, tmp_path, monkeypatch):
    original = StrategyWorkflowService.backtest

    def failure(self, context, policy):
        if context.output_path.name != "baseline":
            raise ValueError("fixture failure")
        return original(self, context, policy)

    monkeypatch.setattr(StrategyWorkflowService, "backtest", failure)
    output = tmp_path / "failed"
    assert run(inputs, output)["failed_count"] == 4
    assert not (output / "best-config.toml").exists()
    assert all(
        item["status"] == "failed" for item in read_json(output / "checkpoint.json").values()
    )
    monkeypatch.setattr(StrategyWorkflowService, "backtest", original)
    assert run(inputs, output, resume=True)["status"] == "complete"


def test_shards_merge_deduplicate_and_reject_incompatible_experiments(inputs, tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    run(inputs, left, shard_count=2, shard_index=0)
    run(inputs, right, shard_count=2, shard_index=1)
    assert set(metrics(left)).isdisjoint(metrics(right))
    combined = tmp_path / "combined"
    merged = merge_optimizations([left, right], combined)
    assert merged["status"] == "complete"
    assert merged["unique_evaluated_count"] == merged["candidate_count"] == 4
    assert merged["total_recorded_trials"] == 4
    duplicate = merge_optimizations([left, right, left], tmp_path / "duplicate")
    assert duplicate["unique_evaluated_count"] == 4
    assert duplicate["total_recorded_trials"] == 6
    assert merge_optimizations([left], tmp_path / "partial")["status"] == "incomplete"
    assert (
        main(
            [
                "backtest",
                "opt-report",
                "--input",
                str(left),
                "--input",
                str(right),
                "--output",
                str(tmp_path / "cli-report"),
            ]
        )
        == 0
    )
    run(inputs, tmp_path / "different", start_date=date(2026, 9, 2))
    with pytest.raises(ValueError, match="incompatible"):
        merge_optimizations([left, tmp_path / "different"], tmp_path / "bad")


def test_no_feasible_candidate_and_corrupt_artifact_are_handled(inputs, tmp_path):
    opt = inputs[1]
    text = opt.read_text().replace(
        "[objective]",
        """[[constraints]]
metric = "trade_count"
operator = ">"
value = 100
[objective]""",
    )
    opt.write_text(text)
    output = tmp_path / "none"
    manifest = run(inputs, output)
    assert manifest["status"] == "complete"
    assert manifest["eligible_count"] == 0
    assert not (output / "best-config.toml").exists()
    first = read_json(output / "selected.json")[0]["id"]
    (output / "trials" / first / "metrics.json").write_text("{}")
    with pytest.raises(ValueError):
        run(inputs, output, resume=True)


def test_warmup_and_invalid_runtime_options_fail_before_writes(inputs, tmp_path):
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="WARMUP"):
        run(inputs, output, start_date=date(2025, 6, 16), end_date=date(2025, 6, 18))
    assert not output.exists()
    for kwargs in (
        {"workers": 0},
        {"shard_count": 2, "shard_index": 2},
        {"resume": True, "existing": "overwrite"},
    ):
        with pytest.raises(ValueError, match="OPT_CONFIG_INVALID"):
            run(inputs, output, **kwargs)


def test_invalid_metric_and_dry_run_diagnostics_never_write(inputs, tmp_path):
    inputs[1].write_text(inputs[1].read_text().replace('metric = "sharpe"', 'metric = "sharp"'))
    output = tmp_path / "not-created"
    log = tmp_path / "dry-log.jsonl"
    failure = tmp_path / "dry-failure"
    assert (
        main(
            [
                "--log-file",
                str(log),
                "backtest",
                "opt",
                "--config",
                str(inputs[0]),
                "--opt-config",
                str(inputs[1]),
                "--output",
                str(output),
                "--failure-report",
                str(failure),
                "--dry-run",
            ]
        )
        == 2
    )
    assert not any(path.exists() for path in (output, log, failure))


def test_parallel_interrupt_keeps_checkpoint_and_best_export_consistent(inputs, tmp_path):
    output = tmp_path / "parallel-interrupted"

    def interrupt_after_checkpoint(message):
        if message.startswith("OPT_PROGRESS"):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_optimization(
            config=inputs[0],
            opt_config=inputs[1],
            output=output,
            workers=2,
            progress=interrupt_after_checkpoint,
        )
    assert read_json(output / "study_manifest.json")["status"] == "interrupted"
    assert len(read_json(output / "checkpoint.json")) == 1
    if (output / "best.json").exists():
        best = read_json(output / "best.json")
        config = tomllib.loads((output / "best-config.toml").read_text())
        assert (
            config["strategy"]["entry_confirmation_ma_days"]
            == best["parameters"]["entry_confirmation_ma_days"]
        )
        assert (
            config["strategy"]["entry_confirmation_window_days"]
            == best["parameters"]["entry_confirmation_window_days"]
        )
    assert run(inputs, output, resume=True, workers=2)["status"] == "complete"


@pytest.mark.parametrize("workers", [1, 2])
def test_opt_and_plain_backtest_accept_closed_endpoints_without_extending_period(
    inputs, tmp_path, workers
):
    # Sundays border the range. Prices on the following Monday exist in the fixture
    # and must not enter readiness, valuation, fills, or metrics.
    start, end = date(2026, 8, 30), date(2026, 9, 6)
    output = tmp_path / "holiday-study"
    assert (
        main(
            [
                "backtest",
                "opt",
                "--config",
                str(inputs[0]),
                "--opt-config",
                str(inputs[1]),
                "--output",
                str(output),
                "--start-date",
                str(start),
                "--end-date",
                str(end),
                "--workers",
                str(workers),
            ]
        )
        == 0
    )
    for path in [output / "baseline", *(output / "trials").iterdir()]:
        if path.name.startswith("."):
            continue
        actual = read_json(path / "metrics.json")
        requested = read_json(path / "resolved_config.json")
        assert actual["date_start"] == "2026-08-31"
        assert actual["date_end"] == "2026-09-04"
        assert requested["start_date"] == str(start)
        assert requested["end_date"] == str(end)
        fills = pq.read_table(path / "trades.parquet", columns=["execution_date"]).to_pylist()
        assert all(
            date(2026, 8, 31) <= item["execution_date"] <= date(2026, 9, 4) for item in fills
        )
    plain = tmp_path / "plain"
    assert (
        main(
            [
                "backtest",
                "run",
                "--config",
                str(inputs[0]),
                "--output",
                str(plain),
                "--start-date",
                str(start),
                "--end-date",
                str(end),
            ]
        )
        == 0
    )
    assert read_json(plain / "metrics.json") == read_json(output / "baseline/metrics.json")
    trading_days_only = tmp_path / "trading-days-only"
    assert (
        main(
            [
                "backtest",
                "run",
                "--config",
                str(inputs[0]),
                "--output",
                str(trading_days_only),
                "--start-date",
                "2026-08-31",
                "--end-date",
                "2026-09-04",
            ]
        )
        == 0
    )
    assert read_json(trading_days_only / "metrics.json") == read_json(plain / "metrics.json")


def test_plain_backtest_rejects_range_without_trading_days_before_publication(inputs, tmp_path):
    output = tmp_path / "empty-range"
    assert (
        main(
            [
                "backtest",
                "run",
                "--config",
                str(inputs[0]),
                "--output",
                str(output),
                "--start-date",
                "2026-09-05",
                "--end-date",
                "2026-09-06",
            ]
        )
        == 3
    )
    assert not output.exists()


@pytest.fixture
def reselect_inputs(inputs, tmp_path):
    _, opt = inputs
    opt.write_text(
        opt.read_text().replace(
            "[objective]",
            '[[constraints]]\nmetric = "trade_count"\noperator = ">"\nvalue = 100\n[objective]',
        )
    )
    source = tmp_path / "source"
    assert run(inputs, source, start_date=date(2026, 9, 2))["eligible_count"] == 0
    relaxed = tmp_path / "relaxed.toml"
    relaxed.write_text(
        (source / "input-opt-config.toml").read_text().replace("value = 100", "value = 0")
    )
    return source, relaxed


def test_reselection_cli_reuses_ineligible_results_without_backtesting(
    reselect_inputs, tmp_path, monkeypatch, capsys
):
    source, opt = reselect_inputs
    before = {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*")
        if path.is_file()
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("reselection must not run backtests, workers or code fingerprints")

    monkeypatch.setattr(StrategyWorkflowService, "backtest", forbidden)
    monkeypatch.setattr("xqatexp.optimization.runner.ProcessPoolExecutor", forbidden)
    monkeypatch.setattr("xqatexp.application.optimization.code_fingerprint", forbidden)
    output = tmp_path / "reselected"
    args = [
        "backtest",
        "opt-report",
        "--input",
        str(source),
        "--opt-config",
        str(opt),
        "--output",
        str(output),
    ]
    assert main(args) == 0
    assert "new_backtests=0" in capsys.readouterr().out
    manifest = read_json(output / "study_manifest.json")
    assert manifest["operation"] == "reselect"
    assert manifest["reused_count"] == 4
    assert manifest["eligible_count"] > 0
    assert manifest["new_backtest_count"] == 0
    assert not (output / "trials").exists()
    best = read_json(output / "best.json")
    assert best["artifact_path"] == str(source / "trials" / best["id"])
    assert best["metrics"] == metrics(source)[best["id"]]
    config = tomllib.loads((output / "best-config.toml").read_text())
    assert config["start_date"] == "2026-09-02"  # Preserve the original CLI override.
    assert config["end_date"] == "2026-09-07"
    assert config["strategy"]["buy_fraction"] == 0.20
    assert config["execution"]["slippage_bps"] == 20
    context = resolve_config(
        {}, output / "best-config.toml", run_id="check", generated_at=datetime.now(UTC)
    )
    from xqatexp.reporting.structured import resolved_context_value

    actual = resolved_context_value(context)
    saved = read_json(source / "trials" / best["id"] / "resolved_config.json")
    actual.pop("output_alias")
    saved.pop("output_alias")
    assert digest(actual) == digest(saved)
    with (output / "results.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4
    assert all(row["artifact_path"] == str(source / "trials" / row["id"]) for row in rows)
    assert (
        read_json(output / "sources.json")[0]["code"]
        == read_json(source / "study_manifest.json")["identity"]["compatibility"]["code"]
    )
    report = (output / "report.md").read_text()
    assert "新增回测次数: 0" in report and "原约束: trade_count > 100" in report
    assert "Decimal(" not in report
    assert reselect_optimization([source], opt, output, existing="skip")["status"] == "complete"
    assert before == {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*")
        if path.is_file()
    }
    monkeypatch.undo()
    with pytest.raises(ValueError, match="derived report"):
        reselect_optimization([output], opt, tmp_path / "again")
    with pytest.raises(ValueError, match="derived report"):
        merge_optimizations([output], tmp_path / "merge-derived")
    with pytest.raises(ValueError, match="derived report"):
        run_optimization(
            config=source / "input-config.toml", opt_config=opt, output=output, resume=True
        )


def test_reselection_dry_run_and_changed_objective(reselect_inputs, tmp_path, capsys):
    source, opt = reselect_inputs
    opt.write_text(
        opt.read_text()
        .replace('metric = "sharpe"', 'metric = "annualized_return"')
        .replace('direction = "maximize"', 'direction = "minimize"')
    )
    output, log, failure = (
        tmp_path / "new-parent" / "report",
        tmp_path / "log.jsonl",
        tmp_path / "failure",
    )
    args = [
        "--log-file",
        str(log),
        "backtest",
        "opt-report",
        "--input",
        str(source),
        "--opt-config",
        str(opt),
        "--output",
        str(output),
        "--failure-report",
        str(failure),
    ]
    assert main([*args, "--dry-run"]) == 0
    assert "OPT_DRY_RUN" in capsys.readouterr().out
    assert not output.parent.exists() and not log.exists() and not failure.exists()
    reselect_optimization([source], opt, output)
    best = read_json(output / "best.json")
    from xqatexp.optimization.models import StudySpec

    spec = StudySpec.load(opt)
    candidates = [
        item
        for item in read_json(source / "checkpoint.json").values()
        if not spec.rejection_reasons(item["metrics"])
    ]
    expected = min((result_from_value(item) for item in candidates), key=spec.rank_key)
    assert best["id"] == expected.id
    assert "目标值 (annualized_return)" in (output / "report.md").read_text()
    opt.write_text(opt.read_text().replace('metric = "annualized_return"', 'metric = "typo"'))
    assert main([*args, "--output", str(tmp_path / "invalid"), "--dry-run"]) == 2
    assert not log.exists() and not failure.exists() and not (tmp_path / "invalid").exists()
    assert (
        merge_optimizations([source], tmp_path / "dry-merge", dry_run=True)["status"] == "dry_run"
    )
    assert not (tmp_path / "dry-merge").exists()


def test_reselection_rejects_changes_before_overwriting_and_removes_stale_best(
    reselect_inputs, tmp_path
):
    source, opt = reselect_inputs
    output = tmp_path / "report"
    reselect_optimization([source], opt, output)
    before = collect_files(output)
    text = opt.read_text()
    for changed in (
        text.replace('start_date = "2026-09-01"', 'start_date = "2026-09-02"'),
        text.replace("entry_confirmation_ma_days = [2, 3]", "entry_confirmation_ma_days = [2]"),
        text.replace('entry_confirmation_mode = "ma_rebound"', 'entry_confirmation_mode = "none"'),
    ):
        opt.write_text(changed)
        with pytest.raises(ValueError, match="only objective"):
            reselect_optimization([source], opt, output, existing="overwrite")
        assert collect_files(output) == before
    opt.write_text(text.replace("value = 0", "value = 100"))
    with pytest.raises(ValueError, match="inputs differ"):
        reselect_optimization([source], opt, output, existing="skip")
    assert reselect_optimization([source], opt, output, existing="overwrite")["eligible_count"] == 0
    assert not (output / "best.json").exists() and not (output / "best-config.toml").exists()
    assert not list(tmp_path.glob(".report.backup-*"))
    with pytest.raises(ValueError, match="exactly one"):
        reselect_optimization([source, source], opt, tmp_path / "multi")
    with pytest.raises(ValueError, match="overlap"):
        reselect_optimization([source], opt, source / "nested")
    opt.write_text(text.replace("workers = 1", "workers = 8"))
    assert reselect_optimization([source], opt, tmp_path / "workers")["status"] == "complete"


def test_reselection_rejects_incomplete_and_corrupt_source(reselect_inputs, tmp_path):
    source, opt = reselect_inputs
    output = tmp_path / "report"
    manifest_path = source / "study_manifest.json"
    manifest = read_json(manifest_path)
    write_json(manifest_path, {**manifest, "full_grid_complete": False})
    with pytest.raises(ValueError, match="complete successful grid"):
        reselect_optimization([source], opt, output)
    assert not output.exists()
    write_json(manifest_path, manifest)
    checkpoint = read_json(source / "checkpoint.json")
    removed_id = next(iter(checkpoint))
    removed = checkpoint.pop(removed_id)
    write_json(source / "checkpoint.json", checkpoint)
    manifest["files"]["checkpoint.json"] = hashlib.sha256(
        (source / "checkpoint.json").read_bytes()
    ).hexdigest()
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="complete candidate set"):
        reselect_optimization([source], opt, output)
    assert not output.exists()
    checkpoint[removed_id] = removed
    write_json(source / "checkpoint.json", checkpoint)
    manifest["files"]["checkpoint.json"] = hashlib.sha256(
        (source / "checkpoint.json").read_bytes()
    ).hexdigest()
    write_json(manifest_path, manifest)
    (source / "trials" / removed_id / "metrics.json").write_text("{}")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        reselect_optimization([source], opt, output)
    assert not output.exists()


@pytest.mark.parametrize("failure_stage", ["report", "manifest"])
def test_reselection_detects_source_change_and_publication_failure(
    reselect_inputs, tmp_path, monkeypatch, failure_stage
):
    source, opt = reselect_inputs
    output = tmp_path / "report"

    def change_source(message):
        path = source / "study_manifest.json"
        path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="source changed"):
        reselect_optimization([source], opt, output, progress=change_source)
    assert not output.exists()
    reselect_optimization([source], opt, output)
    original = collect_files(output)

    def fail_publish(*args, **kwargs):
        raise OSError("fixture publication failure")

    if failure_stage == "report":
        monkeypatch.setattr("xqatexp.application.optimization.publish_study_report", fail_publish)
    else:

        def fail_complete(path, value):
            if path == output / "study_manifest.json" and value.get("status") == "complete":
                raise OSError("fixture publication failure")
            write_json(path, value)

        monkeypatch.setattr("xqatexp.application.optimization.write_json", fail_complete)
    with pytest.raises(OSError, match="publication failure"):
        reselect_optimization([source], opt, output, existing="overwrite")
    assert read_json(output / "study_manifest.json")["status"] == "running"
    backups = list(tmp_path.glob(".report.backup-*"))
    assert len(backups) == 1 and collect_files(backups[0]) == original
