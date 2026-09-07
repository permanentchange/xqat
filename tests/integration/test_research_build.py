from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.cli import main
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.client import QueryResult
from xqatexp.providers.tushare.raw import FetchRequest, RawFetchService
from xqatexp.providers.tushare.registry import get_dataset
from xqatexp.research.tables import ResearchBuildConfig, ResearchBuilder, ResearchCheckService


class _RecordsClient:
    def __init__(self, dataset_id: str, records: tuple[dict[str, object], ...]) -> None:
        self.spec = get_dataset(dataset_id)
        self.records = records

    def query(self, api_name, fields, params):
        assert api_name == self.spec.api_name
        return QueryResult(tuple(fields), self.records, 1)


def _raw(
    root: Path,
    dataset_id: str,
    records: tuple[dict[str, object], ...],
    *,
    slice_date: date = date(2026, 9, 4),
) -> Path:
    output = root / dataset_id
    RawFetchService(_RecordsClient(dataset_id, records)).fetch(
        FetchRequest(
            dataset_id=dataset_id,
            start_date=slice_date,
            end_date=slice_date,
            security_ids=(
                (str(records[0]["ts_code"]),)
                if dataset_id in {"income", "fina_indicator", "dividend"}
                else ()
            ),
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
    assert actions[0]["cash_per_share_before_tax"] == Decimal("0.080000")
    assert actions[0]["cash_per_share_after_tax"] == Decimal("0.100000")
    factors = pq.read_table(output / "tables/system_factor_daily.parquet").to_pylist()
    factor_ids = {row["factor_id"] for row in factors}
    assert {
        "total_mv_pct_v1",
        "amount_20d_pct_v1",
        "momentum_60_ex5_v1",
        "momentum_40_v1",
        "trend_stability_60_v1",
        "volume_price_confirm_20_v1",
        "volatility_20_v1",
        "roe_annualized_v1",
        "profit_positive_ttm_v1",
        "consecutive_loss_2_v1",
    } == factor_ids
    report = ResearchCheckService().check(output)
    assert report.valid is True
    assert report.issue_codes == ()

    cli_report = tmp_path / "research-check.json"
    assert (
        main(["data", "check-research", "--input", str(output), "--report", str(cli_report)]) == 0
    )
    assert '"valid": true' in cli_report.read_text(encoding="utf-8")

    config_path = tmp_path / "build.toml"
    config_path.write_text(
        'schema_version = "1.0"\nstart_date = "2026-09-04"\nend_date = "2026-09-04"\n'
        '[strategy]\ncsi300_etf_id = "510300.SH"\n',
        encoding="utf-8",
    )
    cli_output = tmp_path / "research-cli"
    arguments = ["data", "build", "--config", str(config_path), "--output", str(cli_output)]
    for raw_path in raw_inputs:
        arguments.extend(("--raw-root", str(raw_path)))
    assert main(arguments) == 0
    assert ResearchCheckService().check(cli_output).valid is True


def test_research_check_reports_tampered_table(tmp_path: Path) -> None:
    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    output = tmp_path / "research"
    market = output / "tables/market_daily.parquet"
    market.write_bytes(market.read_bytes() + b"tampered")

    report = ResearchCheckService().check(output)
    assert report.valid is False
    assert report.issue_codes == ("ARTIFACT_HASH_MISMATCH",)


def test_research_builder_rejects_output_overlapping_direct_inputs(tmp_path: Path) -> None:
    config = ResearchBuildConfig(
        date(2026, 9, 4),
        date(2026, 9, 4),
        "510300.SH",
        OverwritePolicy.ERROR,
    )
    shared = tmp_path / "shared"
    with pytest.raises(ValueError, match="CONFIG_PATH_CONFLICT"):
        ResearchBuilder().build((shared,), config, shared)
    with pytest.raises(ValueError, match="CONFIG_PATH_CONFLICT"):
        ResearchBuilder().update(shared, (), config, shared / "result")


def test_full_day_suspension_has_status_even_without_daily_bar(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw-suspended"
    first = date(2026, 9, 3)
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
            slice_date=first,
        ),
        _raw(
            raw_root,
            "trade_calendar",
            (
                {
                    "exchange": "SSE",
                    "cal_date": "20260903",
                    "is_open": 1,
                    "pretrade_date": "20260902",
                },
                {
                    "exchange": "SSE",
                    "cal_date": "20260904",
                    "is_open": 1,
                    "pretrade_date": "20260903",
                },
            ),
            slice_date=first,
        ),
        _raw(
            raw_root,
            "stock_daily",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260903",
                    "open": 10,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10,
                    "pre_close": 10,
                    "change": 0,
                    "pct_chg": 0,
                    "vol": 100,
                    "amount": 100,
                },
            ),
            slice_date=first,
        ),
        _raw(
            raw_root,
            "stock_adj_factor",
            ({"ts_code": "600000.SH", "trade_date": "20260903", "adj_factor": 1},),
            slice_date=first,
        ),
        _raw(
            raw_root,
            "stock_daily_basic",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260903",
                    "close": 10,
                    "turnover_rate": 1,
                    "total_mv": 100,
                    "circ_mv": 80,
                },
            ),
            slice_date=first,
        ),
        _raw(
            raw_root,
            "stock_price_limit",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260903",
                    "pre_close": 10,
                    "up_limit": 11,
                    "down_limit": 9,
                },
            ),
            slice_date=first,
        ),
        _raw(raw_root, "stock_st_status", (), slice_date=first),
        _raw(
            raw_root,
            "stock_suspend",
            (
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260904",
                    "suspend_timing": None,
                    "suspend_type": "S",
                },
            ),
            slice_date=date(2026, 9, 4),
        ),
    )
    output = tmp_path / "research-suspended"
    ResearchBuilder().build(
        raw_inputs,
        ResearchBuildConfig(first, date(2026, 9, 4), "510300.SH", OverwritePolicy.ERROR),
        output,
    )
    statuses = pq.read_table(output / "tables/security_status_daily.parquet").to_pylist()
    suspended = next(row for row in statuses if row["trade_date"] == date(2026, 9, 4))
    assert suspended["is_suspended_full_day"] is True
