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
        _raw(
            raw_root,
            "income",
            (
                {
                    "ts_code": "600000.SH",
                    "ann_date": "20260902",
                    "f_ann_date": "20260903",
                    "end_date": "20260630",
                    "report_type": "1",
                    "comp_type": "1",
                    "n_income_attr_p": 2.5,
                    "update_flag": "1",
                },
            ),
        ),
        _raw(
            raw_root,
            "fina_indicator",
            (
                {
                    "ts_code": "600000.SH",
                    "ann_date": "20260903",
                    "end_date": "20260630",
                    "roe": 1.0,
                    "roe_waa": 1.1,
                    "roe_yearly": 12.5,
                    "profit_dedt": 2.0,
                    "update_flag": "1",
                },
            ),
        ),
        _raw(
            raw_root,
            "dividend",
            (
                {
                    "ts_code": "600000.SH",
                    "end_date": "20251231",
                    "ann_date": "20260901",
                    "div_proc": "实施",
                    "stk_div": 0.0,
                    "stk_bo_rate": 0.0,
                    "stk_co_rate": 0.0,
                    "cash_div": 0.10,
                    "cash_div_tax": 0.08,
                    "record_date": "20260903",
                    "ex_date": "20260904",
                    "pay_date": "20260904",
                    "div_listdate": None,
                    "imp_ann_date": "20260902",
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
    financial = pq.read_table(output / "tables/financial_snapshot.parquet").to_pylist()
    assert financial[0]["available_from"] == date(2026, 9, 4)
    assert financial[0]["net_profit_parent_ytd"] == Decimal("25000.0000")
    assert financial[0]["roe_annualized"] == Decimal("0.125000000000")
    actions = pq.read_table(output / "tables/corporate_action.parquet").to_pylist()
    assert actions[0]["action_type"] == "CASH_DIVIDEND"
    assert actions[0]["cash_per_share_after_tax"] == Decimal("0.080000")
