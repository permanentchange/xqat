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
