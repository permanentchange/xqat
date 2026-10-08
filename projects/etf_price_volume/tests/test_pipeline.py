from __future__ import annotations

from dataclasses import replace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from projects.etf_price_volume.research import pipeline
from projects.etf_price_volume.research.config import load
from projects.etf_price_volume.research.data import load_data
from projects.etf_price_volume.research.features import build_features
from projects.etf_price_volume.run import main

from .conftest import refresh_manifest


def test_plan_does_not_write_or_connect(inputs, tmp_path, monkeypatch, capsys):
    import socket

    def deny(*args, **kwargs):
        raise AssertionError("offline plan attempted network access")

    monkeypatch.setattr(socket.socket, "connect", deny)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert main(["plan", "--config", str(inputs.source)]) == 0
    assert '"tests": 72' in capsys.readouterr().out
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


def test_holdout_prices_never_enter_basic_checks_or_features(inputs):
    original, _ = load_data(inputs)
    market = inputs.input / "tables/market_daily.parquet"
    table = pq.read_table(market)
    rows = table.to_pylist()
    for row in rows:
        if row["trade_date"] >= inputs.holdout:
            row.update(open_raw=-1.0, high_raw=-1.0, research_close=-1.0, volume_shares=-1)
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), market)
    refresh_manifest(inputs.input)
    modified, quality = load_data(inputs)
    assert quality["valid"]
    assert original.equals(modified)
    assert max(modified["trade_date"].to_pylist()) <= inputs.end
    assert build_features(original)[0].equals(build_features(modified)[0])


def test_pipeline_publishes_all_stages_and_resumes_without_recomputing(
    inputs, tmp_path, monkeypatch
):
    output = tmp_path / "result"
    pipeline.run(inputs, output, False, progress=lambda _: None)
    manifest = pipeline.read(output / "manifest.json")
    assert manifest["status"] == "complete"
    assert tuple(manifest["stages"]) == tuple(sorted(pipeline.STAGES))  # canonical JSON sorts keys
    assert len(pipeline.read(output / "statistics/tests.json")) == 72
    assert len(list((output / "report").glob("*.png"))) == 3
    labels = pq.read_table(output / "labels/labels.parquet")
    assert all(d is None or d <= inputs.end for d in labels["exit_date_5"].to_pylist())
    assert "回顾性保留集" in (output / "report.md").read_text("utf-8")

    def deny(*args, **kwargs):
        raise AssertionError("completed stage was recomputed")

    monkeypatch.setattr(pipeline, "produce", deny)
    pipeline.run(inputs, output, True, progress=lambda _: None)
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        pipeline.run(inputs, output, False)
    with pytest.raises(ValueError, match="INCOMPATIBLE"):
        pipeline.run(replace(inputs, raw={**inputs.raw, "workers": 2}), output, True)
    (output / "features/definitions.json").write_text("{}")
    with pytest.raises(ValueError, match="CORRUPT"):
        pipeline.run(inputs, output, True)


def test_interrupted_stage_reuses_completed_predecessors(inputs, tmp_path, monkeypatch):
    output = tmp_path / "interrupted"
    real = pipeline.produce

    def stop(stage, *args):
        if stage == "labels":
            raise KeyboardInterrupt
        real(stage, *args)

    monkeypatch.setattr(pipeline, "produce", stop)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(inputs, output, False, progress=lambda _: None)
    assert set(pipeline.read(output / "manifest.json")["stages"]) == {"data", "features"}
    assert not list(output.glob("*.staging-*"))
    messages = []
    monkeypatch.setattr(pipeline, "produce", real)
    pipeline.run(inputs, output, True, progress=messages.append)
    assert "RESEARCH_REUSED stage=data" in messages
    assert "RESEARCH_REUSED stage=features" in messages


def test_publication_before_root_checkpoint_is_recovered(inputs, tmp_path, monkeypatch):
    output = tmp_path / "recovery"
    real = pipeline.write
    crashed = False

    def crash(path, value):
        nonlocal crashed
        if path == output / "manifest.json" and "data" in value.get("stages", {}) and not crashed:
            crashed = True
            raise KeyboardInterrupt
        real(path, value)

    monkeypatch.setattr(pipeline, "write", crash)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(inputs, output, False, progress=lambda _: None)
    assert (output / "data/manifest.json").is_file()
    assert pipeline.read(output / "manifest.json")["stages"] == {}
    monkeypatch.setattr(pipeline, "write", real)
    pipeline.run(inputs, output, True, progress=lambda _: None)
    assert pipeline.read(output / "manifest.json")["status"] == "complete"


def test_changed_input_blocks_resume_before_writing(inputs, tmp_path, monkeypatch):
    output = tmp_path / "changed"
    real = pipeline.produce

    def stop(stage, *args):
        if stage == "features":
            raise KeyboardInterrupt
        real(stage, *args)

    monkeypatch.setattr(pipeline, "produce", stop)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(inputs, output, False, progress=lambda _: None)
    market = inputs.input / "tables/market_daily.parquet"
    table = pq.read_table(market)
    rows = table.to_pylist()
    rows[2]["volume_shares"] += 1
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), market)
    refresh_manifest(inputs.input)
    before = (output / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="INCOMPATIBLE"):
        pipeline.run(inputs, output, True)
    assert (output / "manifest.json").read_bytes() == before


def test_invalid_data_publishes_short_check_and_stops(inputs, tmp_path):
    market = inputs.input / "tables/market_daily.parquet"
    table = pq.read_table(market)
    pq.write_table(table.slice(1), market)
    # First observed bar shifts forward: remove an internal day as well.
    table = pq.read_table(market)
    pq.write_table(pa.concat_tables([table.slice(0, 10), table.slice(11)]), market)
    refresh_manifest(inputs.input)
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="DATA_INVALID"):
        pipeline.run(inputs, output, False)
    assert pipeline.read(output / "manifest.json")["status"] == "blocked_data"
    assert (output / "data/quality.md").is_file()
    assert not (output / "features").exists()


def test_output_conflicts_and_symlinks_are_rejected(inputs, tmp_path):
    with pytest.raises(ValueError, match="PATH_CONFLICT"):
        pipeline.run(inputs, inputs.input / "output", False)
    link = tmp_path / "linked"
    link.symlink_to(inputs.input, target_is_directory=True)
    with pytest.raises(Exception, match="symlink"):
        pipeline.run(inputs, link / "output", False)


@pytest.mark.parametrize(
    "old,new",
    [
        ('holdout_start = "2015-01-01"', 'holdout_start = "2014-01-01"'),
        ("workers = 1", "workers = true"),
        ("bootstrap_samples = 100", "bootstrap_samples = 0"),
        ("horizons = [5]", "horizons = [5, 5]"),
        ("fdr_alpha = 0.05", "fdr_alpha = nan"),
    ],
)
def test_invalid_configuration_is_rejected(inputs, old, new):
    inputs.source.write_text(inputs.source.read_text("utf-8").replace(old, new), "utf-8")
    with pytest.raises(ValueError, match="CONFIG_INVALID"):
        load(inputs.source)
