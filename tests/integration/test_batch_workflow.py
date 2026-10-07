from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from tests.unit.providers.test_batch_fetch import _plan
from tests.unit.providers.test_batch_fetch import market as market
from xqatexp.cli import main
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.batch import BatchRunner
from xqatexp.providers.tushare.collection import collection_roots, index_collection, read_index


def test_full_cli_bootstrap_update_and_research_build(market, tmp_path: Path, monkeypatch) -> None:
    import pyarrow.parquet as pq

    import xqatexp.providers.tushare.runtime as runtime_module
    from xqatexp.research.tables import ResearchBuildConfig, ResearchBuilder, ResearchCheckService

    transport, _ = market
    original_http = httpx.Client
    monkeypatch.setattr(
        runtime_module.httpx,
        "Client",
        lambda **kwargs: original_http(transport=httpx.MockTransport(transport), **kwargs),
    )
    monkeypatch.setenv("TUSHARE_TOKEN", "test-secret-value")
    plan_file = tmp_path / "plan.toml"
    plan_file.write_text("""start="2026-09-04"
end="2026-09-08"
period_start="2026-03-31"
period_end="2026-03-31"
datasets=["stock_daily","stock_adj_factor","stock_daily_basic","stock_price_limit",
          "stock_suspend","stock_st_status","income","fina_indicator"]
[[requests]]
dataset="stock_basic"
[[requests]]
dataset="fund_basic"
[[requests]]
dataset="fund_daily"
security_id="510300.SH"
[[requests]]
dataset="fund_adj_factor"
security_id="510300.SH"
[[requests]]
dataset="index_daily"
[[requests]]
dataset="dividend"
security_id="*"
""")
    provider_file = tmp_path / "provider.toml"
    provider_file.write_text("calls_per_minute=6000000\nworkers=4\nmax_attempts=1\n")
    config = tmp_path / "research.toml"
    config.write_text(
        'schema_version="1.0"\nstart_date="2026-09-04"\nend_date="2026-09-08"\n[strategy]\ncsi300_etf_id="510300.SH"\n'
    )
    root, output = tmp_path / "collection", tmp_path / "research"
    command = [
        "data",
        "fetch-batch",
        "--plan",
        str(plan_file),
        "--provider-config",
        str(provider_file),
        "--output",
        str(root),
    ]
    assert main(command) == 0
    assert (
        main(
            [
                "data",
                "build",
                "--raw-collection",
                str(root),
                "--config",
                str(config),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert ResearchCheckService().check(output).valid
    builder = ResearchBuilder()
    raw, _ = builder._load_raw(collection_roots(root))
    build_config = ResearchBuildConfig(
        date(2026, 9, 4), date(2026, 9, 8), "510300.SH", OverwritePolicy.ERROR
    )
    original_master = builder._transform(raw, build_config)["security_master"]
    raw["index_daily"].reverse()
    assert builder._transform(raw, build_config)["security_master"] == original_master
    dividends = [
        entry
        for entry in read_index(root)["entries"]
        if entry["identity"]["dataset_id"] == "dividend"
    ]
    assert {entry["identity"]["parameters"]["ts_code"] for entry in dividends} == {
        "600000.SH",
        "600001.SH",
    }
    assert main([*command, "--mode", "update"]) == 0
    updated = tmp_path / "research-updated"
    assert (
        main(
            [
                "data",
                "build",
                "--raw-collection",
                str(root / "collection.json"),
                "--config",
                str(config),
                "--output",
                str(updated),
            ]
        )
        == 0
    )
    assert ResearchCheckService().check(updated).valid
    for table in output.glob("tables/*.parquet"):
        assert pq.read_table(table) == pq.read_table(updated / "tables" / table.name)
    assert index_collection(root)["complete"]


def test_old_raw_directory_index_matches_explicit_build(tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    from tests.integration.test_research_build import (
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    config = tmp_path / "build.toml"
    config.write_text(
        'schema_version="1.0"\nstart_date="2026-09-04"\nend_date="2026-09-04"\n[strategy]\ncsi300_etf_id="510300.SH"\n'
    )
    assert main(["data", "collection", "index", "--input", str(tmp_path / "raw")]) == 0
    output = tmp_path / "from-collection"
    assert (
        main(
            [
                "data",
                "build",
                "--raw-collection",
                str(tmp_path / "raw"),
                "--config",
                str(config),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    for table in output.glob("tables/*.parquet"):
        assert pq.read_table(table) == pq.read_table(tmp_path / "research" / "tables" / table.name)


def test_extend_calendar_and_repair_historical_window(market, tmp_path: Path) -> None:
    from xqatexp.research.tables import ResearchBuilder

    _, client = market
    root = tmp_path / "collection"
    initial = replace(_plan(), end=date(2026, 9, 4))
    assert BatchRunner(client).run(initial, root)["complete"]
    assert BatchRunner(client).run(_plan(), root, mode="update")["complete"]
    assert BatchRunner(client).run(initial, root, mode="update")["complete"]
    raw, _ = ResearchBuilder()._load_raw(collection_roots(root))
    assert {row["cal_date"] for row in raw["trade_calendar"]} == {
        "20260904",
        "20260905",
        "20260906",
        "20260907",
        "20260908",
    }


def test_financial_conflict_marks_collection_incomplete(market, tmp_path: Path) -> None:
    transport, client = market
    root = tmp_path / "collection"
    assert BatchRunner(client).run(_plan(True), root)["complete"]
    transport.profit = 99.0
    report = BatchRunner(client).run(_plan(True), root, mode="update")
    assert not report["complete"]
    assert any(task["error_code"] == "DATA_CONFLICT" for task in report["tasks"])
    with pytest.raises(ValueError, match="incomplete"):
        collection_roots(root)


def test_regular_and_vip_financial_interfaces_build_identical_snapshots(market, tmp_path: Path):
    from xqatexp.providers.tushare.raw import FetchRequest, RawFetchService
    from xqatexp.research.tables import ResearchBuilder

    _, client = market
    ordinary, vip = [], []
    period = date(2026, 3, 31)
    for dataset in ("income", "fina_indicator"):
        for code in ("600000.SH", "600001.SH"):
            request = FetchRequest(
                dataset,
                period,
                period,
                (code,),
                (),
                tmp_path / f"{dataset}-{code}",
                OverwritePolicy.ERROR,
            )
            ordinary.append(RawFetchService(client).fetch(request).path)
        request = FetchRequest(
            dataset,
            period,
            period,
            (),
            (),
            tmp_path / f"{dataset}-vip",
            OverwritePolicy.ERROR,
            f"{dataset}_vip",
            period,
        )
        vip.append(RawFetchService(client).fetch(request).path)
    builder = ResearchBuilder()
    ordinary_raw, _ = builder._load_raw(ordinary)
    vip_raw, _ = builder._load_raw(vip)
    open_dates = (date(2026, 5, 6),)
    ordinary_rows = builder._financial_rows(ordinary_raw, open_dates)
    assert ordinary_rows == builder._financial_rows(vip_raw, open_dates)
    assert len(ordinary_rows) == 2
    assert all(row["announce_date"] == date(2026, 4, 30) for row in ordinary_rows)
    assert all(row["available_from"] == date(2026, 5, 6) for row in ordinary_rows)
