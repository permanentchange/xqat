from __future__ import annotations

import importlib
import json

import httpx
import pytest

from xqatexp.security import SecretValue


def _client_module():
    try:
        return importlib.import_module("xqatexp.providers.tushare.client")
    except ModuleNotFoundError:
        pytest.fail("Tushare client is not implemented", pytrace=False)


def test_query_maps_field_order_to_complete_records_without_exposing_token() -> None:
    """Catches field/item misalignment or credential retention in returned facts."""
    module = _client_module()
    token = "abcdefgh12345678"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["token"] == token
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": None,
                "data": {
                    "fields": ["ts_code", "trade_date", "close"],
                    "items": [["600000.SH", "20260904", 10.25]],
                },
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = module.TushareClient(SecretValue(token), http_client=http, max_attempts=2)
    result = client.query(
        "daily",
        ("ts_code", "trade_date", "close"),
        {"trade_date": "20260904"},
    )

    assert result.fields == ("ts_code", "trade_date", "close")
    assert result.records == ({"ts_code": "600000.SH", "trade_date": "20260904", "close": 10.25},)
    assert token not in repr(result)


def test_permission_code_is_not_retried_and_error_is_redacted() -> None:
    """Catches wasted retries or token leakage on a stable permission failure."""
    module = _client_module()
    attempts = 0
    token = "abcdefgh12345678"

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"code": 2002, "msg": f"denied {token}", "data": None})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = module.TushareClient(SecretValue(token), http_client=http, max_attempts=5)
    with pytest.raises(Exception, match="DATA_PROVIDER_PERMISSION_DENIED") as captured:
        client.query("fund_daily", ("ts_code",), {"ts_code": "510300.SH"})

    assert attempts == 1
    assert token not in str(captured.value)


def test_http_429_retries_then_returns_success() -> None:
    """Catches a transient response treated as a permanent provider failure."""
    module = _client_module()
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, text="busy")
        return httpx.Response(
            200,
            json={"code": 0, "msg": None, "data": {"fields": ["ts_code"], "items": []}},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = module.TushareClient(
        SecretValue("abcdefgh12345678"),
        http_client=http,
        max_attempts=3,
        sleeper=sleeps.append,
        jitter=lambda: 0.0,
    )
    result = client.query("daily", ("ts_code",), {"trade_date": "20260904"})

    assert result.records == ()
    assert attempts == 2
    assert sleeps == [1.0]


def test_schema_mismatch_identifies_missing_fields() -> None:
    """Catches provider field changes accepted as complete Raw data."""
    module = _client_module()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 0, "msg": None, "data": {"fields": ["ts_code"], "items": []}},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = module.TushareClient(SecretValue("abcdefgh12345678"), http_client=http, max_attempts=1)
    with pytest.raises(Exception, match="DATA_PROVIDER_SCHEMA_MISMATCH"):
        client.query("daily", ("ts_code", "trade_date"), {"trade_date": "20260904"})


def test_token_bucket_waits_before_request_when_capacity_is_exhausted() -> None:
    """Catches configured provider rate limits being silently ignored."""
    module = _client_module()
    current = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        current[0] += seconds

    limiter = module.TokenBucket(
        calls_per_minute=60,
        capacity=1,
        clock=lambda: current[0],
        sleeper=sleep,
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 0, "msg": None, "data": {"fields": ["ts_code"], "items": []}},
        )

    client = module.TushareClient(
        SecretValue("abcdefgh12345678"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=1,
        rate_limiter=limiter,
    )
    client.query("daily", ("ts_code",), {"trade_date": "20260904"})
    client.query("daily", ("ts_code",), {"trade_date": "20260905"})

    assert sleeps == [1.0]


def test_query_all_pages_by_stable_offset_until_short_page() -> None:
    """Catches truncated data or unstable offsets at a provider row limit."""
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        offset = body["params"]["offset"]
        offsets.append(offset)
        rows = {
            0: [["600000.SH"], ["000001.SZ"]],
            2: [["600001.SH"]],
        }[offset]
        return httpx.Response(
            200,
            json={"code": 0, "msg": None, "data": {"fields": ["ts_code"], "items": rows}},
        )

    module = _client_module()
    client = module.TushareClient(
        SecretValue("abcdefgh12345678"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=1,
    )
    result = client.query_all("stock_basic", ("ts_code",), {"exchange": "SSE"}, page_size=2)

    assert offsets == [0, 2]
    assert [row["ts_code"] for row in result.records] == [
        "600000.SH",
        "000001.SZ",
        "600001.SH",
    ]
    assert result.attempts == 2
