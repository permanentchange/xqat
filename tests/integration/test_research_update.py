from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from xqatexp.domain.enums import OverwritePolicy
from xqatexp.research.tables import ResearchBuildConfig, ResearchBuilder


def _table_hashes(path: Path) -> dict[str, str]:
    return {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted((path / "tables").glob("*.parquet"))
    }


def test_no_change_update_republishes_identical_research_tables(tmp_path: Path) -> None:
    # The builder integration test owns transformation coverage; this freezes incremental reuse.
    from tests.integration.test_research_build import (
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    base = tmp_path / "research"
    updated = tmp_path / "research-updated"
    config = ResearchBuildConfig(
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 4),
        csi300_etf_id="510300.SH",
        existing_policy=OverwritePolicy.ERROR,
    )
    ResearchBuilder().update(base, (), config, updated)
    assert _table_hashes(updated) == _table_hashes(base)


def test_incremental_update_matches_full_build_for_new_market_slice(tmp_path: Path) -> None:
    from tests.integration.test_research_build import (
        _raw,
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    delta_root = tmp_path / "delta"
    next_date = date(2026, 9, 5)
    delta = (
        _raw(
            delta_root,
            "trade_calendar",
            (
                {
                    "exchange": "SSE",
                    "cal_date": "20260905",
                    "is_open": 1,
                    "pretrade_date": "20260904",
                },
            ),
            slice_date=next_date,
        ),
        _raw(
            delta_root,
            "stock_daily",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260905",
                    "open": 10.25,
                    "high": 10.8,
                    "low": 10.1,
                    "close": 10.5,
                    "pre_close": 10.25,
                    "change": 0.25,
                    "pct_chg": 2.439,
                    "vol": 3.0,
                    "amount": 4.0,
                },
            ),
            slice_date=next_date,
        ),
        _raw(
            delta_root,
            "stock_adj_factor",
            ({"ts_code": "600000.SH", "trade_date": "20260905", "adj_factor": 1.2},),
            slice_date=next_date,
        ),
        _raw(
            delta_root,
            "stock_daily_basic",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260905",
                    "close": 10.5,
                    "turnover_rate": 1.1,
                    "total_mv": 126.0,
                    "circ_mv": 102.0,
                },
            ),
            slice_date=next_date,
        ),
        _raw(
            delta_root,
            "stock_price_limit",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260905",
                    "pre_close": 10.25,
                    "up_limit": 11.28,
                    "down_limit": 9.23,
                },
            ),
            slice_date=next_date,
        ),
    )
    config = ResearchBuildConfig(date(2026, 9, 4), next_date, "510300.SH", OverwritePolicy.ERROR)
    incremental = tmp_path / "incremental"
    ResearchBuilder().update(tmp_path / "research", delta, config, incremental)
    full = tmp_path / "full"
    base_raw = tuple(sorted((tmp_path / "raw").iterdir()))
    ResearchBuilder().build(base_raw + delta, config, full)

    for table in (
        "security_master",
        "trade_calendar",
        "market_daily",
        "security_status_daily",
        "financial_snapshot",
        "system_factor_daily",
        "corporate_action",
    ):
        incremental_rows = pq.read_table(incremental / f"tables/{table}.parquet").to_pylist()
        full_rows = pq.read_table(full / f"tables/{table}.parquet").to_pylist()
        assert incremental_rows == full_rows
