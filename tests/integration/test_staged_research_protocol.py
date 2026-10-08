from datetime import date, timedelta
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from examples.analyze_staged_drawdown import event_study, run_study, trials
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.demo import create_offline_research


def _event_inputs(tmp_path):
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(140)]
    tables = tmp_path / "tables"
    tables.mkdir()
    pq.write_table(
        pa.Table.from_pylist([{"calendar_date": day, "is_open": True} for day in days]),
        tables / "trade_calendar.parquet",
    )
    initial = ["10.5", "10.4", "10.3", "10.2", "10.1", "10", "9.9", "10.2"]
    rows = [
        {
            "security_id": "510300.SH",
            "trade_date": day,
            "available_from": day,
            "research_open": Decimal("10") + Decimal(i) / 100,
            "research_close": Decimal(initial[i])
            if i < len(initial)
            else Decimal("10") + Decimal(i) / 100,
        }
        for i, day in enumerate(days)
    ]
    pq.write_table(pa.Table.from_pylist(rows), tables / "market_daily.parquet")
    diagnostics = [
        {"decision_date": str(days[i]), "diagnostics": {"values": {"slow_decline": True}}}
        for i in (5, 6, 40)
    ]
    return days, rows, diagnostics


def test_event_groups_confirmations_and_horizons_use_full_windows(tmp_path):
    days, rows, diagnostics = _event_inputs(tmp_path)
    signals, groups, outcomes = event_study(tmp_path, diagnostics, "510300.SH", days[0], days[-1])
    assert len(signals) == 3
    assert len(groups) == 2
    assert groups[0]["signal_days"] == 2
    assert groups[0]["confirmation_date"] == days[7]
    assert groups[0]["delay_trade_days"] == 2
    direct = next(
        r
        for r in outcomes
        if r["group"] == 1 and r["mode"] == "direct" and r["horizon_trade_days"] == 20
    )
    assert direct["entry_date"] == days[6]
    assert direct["end_date"] == days[25]
    assert direct["return"] == rows[25]["research_close"] / rows[6]["research_open"] - 1
    assert direct["max_adverse_return"] == rows[6]["research_close"] / rows[6]["research_open"] - 1
    missing = next(
        r
        for r in outcomes
        if r["group"] == 2 and r["mode"] == "direct" and r["horizon_trade_days"] == 120
    )
    assert missing["status"] == "incomplete"
    assert missing["return"] is None
    # A future price present on disk cannot extend the requested study end date.
    _, _, short = event_study(tmp_path, diagnostics, "510300.SH", days[0], days[24])
    assert all(r["return"] is None for r in short)
    # A signal on the final day must not expose a next-day entry price on disk.
    _, final_day_groups, final_day_outcomes = event_study(
        tmp_path, diagnostics, "510300.SH", days[0], days[5]
    )
    assert final_day_groups[0]["direct_entry_price"] is None
    assert final_day_groups[0]["confirmed_entry_price"] is None
    assert all(r["return"] is None for r in final_day_outcomes)


def test_event_windows_reject_late_available_observations(tmp_path):
    days, rows, diagnostics = _event_inputs(tmp_path)
    rows[20]["available_from"] = days[30]
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "tables/market_daily.parquet")
    _, _, outcomes = event_study(tmp_path, diagnostics, "510300.SH", days[0], days[-1])
    assert all(r["status"] == "incomplete" for r in outcomes if r["group"] == 1)


def _config(tmp_path):
    research = tmp_path / "research"
    create_offline_research(research)
    config = tmp_path / "config.toml"
    config.write_text(
        f'''schema_version = "1.0"
mode = "BACKTEST"
strategy_id = "staged_drawdown_v1"
research_artifact = "{research.as_posix()}"
start_date = "2026-09-02"
end_date = "2026-09-04"
output = "{(tmp_path / "unused").as_posix()}"
[strategy]
security_id = "510300.SH"
''',
        encoding="utf-8",
    )
    return config


def test_offline_protocol_publishes_thirteen_trials_and_refuses_existing_output(tmp_path):
    import hashlib
    import json

    config = _config(tmp_path)
    output = tmp_path / "study"
    run_study(config, date(2026, 9, 2), date(2026, 9, 4), output)
    manifest = json.loads((output / "study_manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert len(manifest["completed_trials"]) == 13
    assert manifest["event_group_count"] == 0
    assert manifest["config_sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
    for trial in trials():
        ArtifactReader().open(output / str(trial["id"]))
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == sha
    before = (output / "study_manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_study(config, date(2026, 9, 2), date(2026, 9, 4), output)
    assert (output / "study_manifest.json").read_bytes() == before


def test_protocol_keeps_failure_evidence_without_claiming_completion(tmp_path, monkeypatch):
    import json

    from xqatexp.application.workflows import StrategyWorkflowService

    config = _config(tmp_path)

    def failure(*args):
        raise RuntimeError("test trial failure")

    monkeypatch.setattr(StrategyWorkflowService, "backtest", failure)
    with pytest.raises(RuntimeError, match="test trial failure"):
        run_study(config, date(2026, 9, 2), date(2026, 9, 4), tmp_path / "failed-study")
    manifest = json.loads((tmp_path / "failed-study/study_manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["completed_trials"] == []
    assert manifest["failure"]["message"] == "test trial failure"


def test_interrupted_protocol_preserves_completed_trial(tmp_path, monkeypatch):
    import json

    from xqatexp.application.workflows import StrategyWorkflowService

    config = _config(tmp_path)
    original = StrategyWorkflowService.backtest
    calls = 0

    def interrupt_after_first(self, *args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(self, *args)

    monkeypatch.setattr(StrategyWorkflowService, "backtest", interrupt_after_first)
    output = tmp_path / "interrupted-study"
    with pytest.raises(KeyboardInterrupt):
        run_study(config, date(2026, 9, 2), date(2026, 9, 4), output)
    manifest = json.loads((output / "study_manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["completed_trials"] == ["reference_100_bps10"]
    assert manifest["failure"]["type"] == "KeyboardInterrupt"
    ArtifactReader().open(output / "reference_100_bps10")
