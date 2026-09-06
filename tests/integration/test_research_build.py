from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.client import QueryResult
from xqatexp.providers.tushare.raw import FetchRequest, RawFetchService
from xqatexp.providers.tushare.registry import get_dataset
from xqatexp.research.tables import ResearchBuildConfig, ResearchBuilder


class _RecordsClient:
    def __init__(self, dataset_id: str, records: tuple[dict[str, object], ...]) -> None:
        self.spec = get_dataset(dataset_id)
        self.records = records

    def query(self, api_name, fields, params):
        assert api_name == self.spec.api_name
        return QueryResult(tuple(fields), self.records, 1)


def _raw(root: Path, dataset_id: str, records: tuple[dict[str, object], ...]) -> Path:
    output = root / dataset_id
    RawFetchService(_RecordsClient(dataset_id, records)).fetch(
        FetchRequest(
            dataset_id=dataset_id,
            start_date=date(2026, 9, 4),
            end_date=date(2026, 9, 4),
            security_ids=(),
            fields=(),
            output_path=output,
            existing_policy=OverwritePolicy.ERROR,
        )
    )
    return output


def test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    raw_inputs = (
        _raw(
            raw_root,
            "stock_basic",
            (
                {
                    "ts_code": "600000.SH",
                    "symbol": "600000",
                    "name": "Example",
                    "market": "主板",
                    "exchange": "SSE",
                    "curr_type": "CNY",
                    "list_status": "L",
                    "list_date": "20200101",
                    "delist_date": None,
                },
            ),
        ),
        _raw(
            raw_root,
            "trade_calendar",
            (
                {
                    "exchange": "SSE",
                    "cal_date": "20260904",
                    "is_open": 1,
                    "pretrade_date": "20260903",
                },
            ),
        ),
        _raw(
            raw_root,
            "stock_daily",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260904",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.9,
                    "close": 10.25,
                    "pre_close": 9.95,
                    "change": 0.3,
                    "pct_chg": 3.0151,
                    "vol": 2.5,
                    "amount": 3.5,
                },
            ),
        ),
        _raw(
            raw_root,
            "stock_adj_factor",
            ({"ts_code": "600000.SH", "trade_date": "20260904", "adj_factor": 1.2},),
        ),
        _raw(
            raw_root,
            "stock_daily_basic",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260904",
                    "close": 10.25,
                    "turnover_rate": 1.0,
                    "total_mv": 123.0,
                    "circ_mv": 100.0,
                },
            ),
        ),
        _raw(
            raw_root,
            "stock_price_limit",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260904",
                    "pre_close": 9.95,
                    "up_limit": 10.95,
                    "down_limit": 8.96,
                },
            ),
        ),
    )
    output = tmp_path / "research"
    result = ResearchBuilder().build(
        raw_inputs,
        ResearchBuildConfig(
            start_date=date(2026, 9, 4),
            end_date=date(2026, 9, 4),
            csi300_etf_id="510300.SH",
            existing_policy=OverwritePolicy.ERROR,
        ),
        output,
    )

    opened = ArtifactReader().open(result.path)
    assert opened.manifest["artifact_type"] == "RESEARCH_DATA"
    assert len(opened.verified_files) == 7
    market = pq.read_table(output / "tables/market_daily.parquet").to_pylist()
    assert market[0]["volume_shares"] == 250
    assert market[0]["amount_cny"] == Decimal("3500.0000")
    assert market[0]["research_close"] == Decimal("10.2500000000")
    status = pq.read_table(output / "tables/security_status_daily.parquet").to_pylist()
    assert status[0]["is_limit_up_locked"] is False
    assert status[0]["is_limit_down_locked"] is False
