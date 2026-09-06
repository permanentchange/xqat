from __future__ import annotations

import gzip
import importlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.domain.enums import OverwritePolicy


class _DailyClient:
    def query(self, api_name, fields, params):
        assert api_name == "daily"
        assert params == {"trade_date": "20260904"}
        return SimpleNamespace(
            fields=fields,
            records=(
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
                    "vol": 123.45,
                    "amount": 678.9,
                },
            ),
            attempts=1,
        )


def test_raw_fetch_preserves_provider_values_and_publishes_verified_artifact(
    tmp_path: Path,
) -> None:
    """Catches premature unit conversion or an unverifiable Raw publication."""
    try:
        raw = importlib.import_module("xqatexp.providers.tushare.raw")
    except ModuleNotFoundError:
        pytest.fail("Raw fetch service is not implemented", pytrace=False)
    output = tmp_path / "stock-daily"
    request = raw.FetchRequest(
        dataset_id="stock_daily",
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 4),
        security_ids=(),
        fields=(),
        output_path=output,
        existing_policy=OverwritePolicy.ERROR,
    )
    raw.RawFetchService(_DailyClient()).fetch(request)

    opened = ArtifactReader().open(output)
    assert opened.manifest["artifact_type"] == "RAW_DATA"
    with gzip.open(output / "response.jsonl.gz", "rt", encoding="utf-8") as stream:
        record = json.loads(stream.readline())
    assert record["vol"] == 123.45
    assert record["amount"] == 678.9
    assert record["_xqat_page_number"] == 1
    request_metadata = json.loads((output / "request.json").read_text(encoding="utf-8"))
    assert request_metadata["api_name"] == "daily"
    assert "token" not in json.dumps(request_metadata).lower()

    report = raw.RawCheckService().check(output)
    assert report.valid is True
    assert report.row_count == 1
    assert report.issue_codes == ()


class _DuplicateDailyClient(_DailyClient):
    def query(self, api_name, fields, params):
        result = super().query(api_name, fields, params)
        return SimpleNamespace(
            fields=result.fields,
            records=result.records + result.records,
            attempts=1,
        )


def test_raw_check_reports_duplicate_business_keys(tmp_path: Path) -> None:
    """Catches conflicting provider facts being silently deduplicated."""
    raw = importlib.import_module("xqatexp.providers.tushare.raw")
    output = tmp_path / "stock-daily"
    request = raw.FetchRequest(
        dataset_id="stock_daily",
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 4),
        security_ids=(),
        fields=(),
        output_path=output,
        existing_policy=OverwritePolicy.ERROR,
    )
    raw.RawFetchService(_DuplicateDailyClient()).fetch(request)

    report = raw.RawCheckService().check(output)
    assert report.valid is False
    assert report.issue_codes == ("DATA_CONFLICT",)


class _RangeClient:
    def __init__(self) -> None:
        self.params = None

    def query(self, api_name, fields, params):
        raise AssertionError("range fetch must use complete paging")

    def query_all(self, api_name, fields, params, *, page_size):
        self.params = params
        assert page_size == 5000
        records = tuple(
            {
                "ts_code": "600000.SH",
                "trade_date": value,
                "open": 10.0,
                "high": 10.5,
                "low": 9.9,
                "close": 10.25,
                "pre_close": 9.95,
                "change": 0.3,
                "pct_chg": 3.0,
                "vol": 100.0,
                "amount": 100.0,
            }
            for value in ("20260903", "20260904")
        )
        return SimpleNamespace(fields=fields, records=records, attempts=2, page_numbers=(1, 2))


def test_raw_fetch_range_uses_paging_and_preserves_page_provenance(tmp_path: Path) -> None:
    raw = importlib.import_module("xqatexp.providers.tushare.raw")
    client = _RangeClient()
    output = tmp_path / "range"
    raw.RawFetchService(client).fetch(
        raw.FetchRequest(
            "stock_daily",
            date(2026, 9, 3),
            date(2026, 9, 4),
            (),
            (),
            output,
            OverwritePolicy.ERROR,
        )
    )
    assert client.params == {"start_date": "20260903", "end_date": "20260904"}
    with gzip.open(output / "response.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    assert [row["_xqat_page_number"] for row in rows] == [1, 2]


class _MasterClient:
    def __init__(self) -> None:
        self.params: list[dict[str, object]] = []

    def query(self, api_name, fields, params):
        raise AssertionError("master fetch must use all explicit slices")

    def query_all(self, api_name, fields, params, *, page_size):
        assert api_name == "stock_basic"
        assert page_size == 5000
        self.params.append(dict(params))
        index = len(self.params)
        row = {
            "ts_code": f"{index:06d}.SH",
            "symbol": f"{index:06d}",
            "name": "Example",
            "market": "主板",
            "exchange": params["exchange"],
            "curr_type": "CNY",
            "list_status": params["list_status"],
            "list_date": "20200101",
            "delist_date": None,
        }
        return SimpleNamespace(fields=fields, records=(row,), attempts=1, page_numbers=(1,))


def test_stock_master_fetches_every_exchange_and_listing_status(tmp_path: Path) -> None:
    raw = importlib.import_module("xqatexp.providers.tushare.raw")
    client = _MasterClient()
    output = tmp_path / "stock-master"
    raw.RawFetchService(client).fetch(
        raw.FetchRequest(
            "stock_basic",
            date(2026, 9, 4),
            date(2026, 9, 4),
            (),
            (),
            output,
            OverwritePolicy.ERROR,
        )
    )
    assert client.params == [
        {"exchange": exchange, "list_status": status}
        for exchange in ("SSE", "SZSE")
        for status in ("L", "D", "P", "G")
    ]
    metadata = json.loads((output / "request.json").read_text(encoding="utf-8"))
    assert metadata["parameters"] == {"slices": client.params}
    with gzip.open(output / "response.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    assert [row["_xqat_page_number"] for row in rows] == list(range(1, 9))
