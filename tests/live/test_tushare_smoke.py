from __future__ import annotations

import os

import pytest

from xqatexp.providers.tushare.client import TushareClient
from xqatexp.providers.tushare.registry import get_dataset
from xqatexp.security import load_tushare_token


@pytest.mark.live_tushare
def test_single_call_daily_mapping_and_permission() -> None:
    if not os.environ.get("TUSHARE_TOKEN", "").strip():
        pytest.skip("SECURITY_SECRET_MISSING: TUSHARE_TOKEN is not in this process")
    spec = get_dataset("stock_daily")
    result = TushareClient(load_tushare_token(os.environ), max_attempts=1).query(
        spec.api_name,
        spec.fields,
        {"trade_date": "20260904"},
    )
    assert set(spec.fields).issubset(result.fields)
    assert result.records
    assert all(row["trade_date"] == "20260904" for row in result.records)
