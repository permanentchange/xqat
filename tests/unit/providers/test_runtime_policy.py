from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from xqatexp.providers.tushare.client import (
    TokenBucket,
    TushareAuthenticationError,
    TushareCancelledError,
    TushareClient,
    TushareParameterError,
    TushareSchemaError,
)
from xqatexp.providers.tushare.runtime import ProviderConfig, ProviderRuntime
from xqatexp.security import SecretValue


def _response(rows=()) -> httpx.Response:
    return httpx.Response(
        200, json={"code": 0, "data": {"fields": ["ts_code"], "items": list(rows)}}
    )


@pytest.mark.parametrize(
    "first",
    [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(503),
        httpx.Response(200, json={"code": 2002, "msg": "每分钟访问限制", "data": None}),
    ],
)
def test_retry_classification_and_retry_after(first: httpx.Response) -> None:
    responses = iter((first, _response()))
    sleeps = []
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as http:
        client = TushareClient(
            SecretValue("offline-test-secret"),
            http_client=http,
            sleeper=sleeps.append,
            jitter=lambda: 0,
            max_attempts=2,
        )
        result = client.query("daily", ("ts_code",), {})
    assert result.attempts == 2
    assert sleeps == ([7.0] if first.status_code == 429 else [1.0])
    assert client.metrics()["requests"] == 2
    assert client.metrics()["retries"] == 1


def test_network_retry_exhaustion_retains_safe_diagnostics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("offline-test-secret", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = TushareClient(
            SecretValue("offline-test-secret"),
            http_client=http,
            sleeper=lambda _: None,
            jitter=lambda: 0,
            max_attempts=2,
        )
        with pytest.raises(Exception, match="network retries exhausted") as error:
            client.query("daily", ("ts_code",), {})
    assert error.value.attempts == 2
    assert "offline-test-secret" not in repr(error.value)


@pytest.mark.parametrize(
    "response,expected",
    [
        (httpx.Response(401), TushareAuthenticationError),
        (httpx.Response(400), TushareParameterError),
        (httpx.Response(200, json={"code": -2001, "msg": "参数错误"}), TushareParameterError),
        (
            httpx.Response(200, json={"code": -2001, "msg": "token invalid"}),
            TushareAuthenticationError,
        ),
        (httpx.Response(200, text="broken"), TushareSchemaError),
        (
            httpx.Response(
                200, json={"code": 0, "data": {"fields": ["ts_code", "ts_code"], "items": []}}
            ),
            TushareSchemaError,
        ),
    ],
)
def test_permanent_errors_are_not_retried(response, expected) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as http:
        client = TushareClient(SecretValue("offline-test-secret"), http_client=http, max_attempts=5)
        with pytest.raises(expected):
            client.query("daily", ("ts_code",), {})
        assert client.metrics()["requests"] == 1


def test_global_and_api_budget_are_consumed_together() -> None:
    clock = [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    global_gate = TokenBucket(calls_per_minute=60, clock=lambda: clock[0], sleeper=sleep)
    api_gate = TokenBucket(calls_per_minute=30, clock=lambda: clock[0], sleeper=sleep)
    moments = []
    for _ in range(4):
        TokenBucket.acquire_many((global_gate, api_gate))
        moments.append(clock[0])
    assert moments == [0, 2, 4, 6]
    api_gate.defer(10)
    TokenBucket.acquire_many((global_gate, api_gate))
    assert clock[0] == 16


def test_threaded_global_budget_cannot_burst() -> None:
    import time

    gate = TokenBucket(calls_per_minute=6000)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: gate.acquire(), range(12)))
    assert time.monotonic() - started >= 0.1


def test_cancellation_interrupts_long_shared_cooldown() -> None:
    cancelled = Event()
    gate = TokenBucket(calls_per_minute=60, cancelled=cancelled)
    gate.defer(60)
    cancelled.set()
    with pytest.raises(TushareCancelledError):
        gate.acquire()


@pytest.mark.parametrize("mode", ["repeated", "changing_fields", "page_limit"])
def test_incomplete_pagination_is_rejected(mode: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = json.loads(request.content)["params"]["offset"]
        if mode == "changing_fields" and offset:
            return httpx.Response(
                200, json={"code": 0, "data": {"fields": ["ts_code", "extra"], "items": [["b", 1]]}}
            )
        return _response((["a" if mode == "repeated" else str(offset)],))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = TushareClient(SecretValue("offline-test-secret"), http_client=http)
        with pytest.raises(TushareSchemaError):
            client.query_all("daily", ("ts_code",), {}, page_size=2, max_pages=3)


def test_runtime_closes_owned_pool_and_sets_cancellation() -> None:
    with ProviderRuntime(SecretValue("offline-test-secret"), ProviderConfig()) as runtime:
        http = runtime._http
        assert not http.is_closed
    assert http.is_closed
    assert runtime.cancelled.is_set()
