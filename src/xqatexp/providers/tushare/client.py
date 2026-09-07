from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any

import httpx

from xqatexp.security import SecretValue


class TushareError(RuntimeError):
    """The provider request failed after applying the client policy."""


class TusharePermissionError(TushareError):
    """The provider rejected access to a requested interface."""


class TushareSchemaError(TushareError):
    """The provider response violated the frozen response contract."""


@dataclass(frozen=True, slots=True)
class QueryResult:
    fields: tuple[str, ...]
    records: tuple[dict[str, Any], ...]
    attempts: int
    page_numbers: tuple[int, ...] = ()


class TokenBucket:
    """A small synchronous token bucket configured outside business logic."""

    def __init__(
        self,
        *,
        calls_per_minute: float,
        capacity: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if calls_per_minute <= 0 or capacity < 1:
            raise ValueError("rate and capacity must be positive")
        self._rate = calls_per_minute / 60.0
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._sleep = sleeper
        self._updated_at = clock()
        self._lock = Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = self._clock()
                elapsed = max(0.0, now - self._updated_at)
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._updated_at = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                self._sleep((1.0 - self._tokens) / self._rate)


class TushareClient:
    def __init__(
        self,
        token: SecretValue,
        *,
        http_client: httpx.Client | None = None,
        max_attempts: int = 5,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: 0.0,
        endpoint: str = "https://api.tushare.pro",
        rate_limiter: TokenBucket | None = None,
    ) -> None:
        if not 1 <= max_attempts <= 8:
            raise ValueError("max_attempts must be in [1, 8]")
        self._token = token
        self._http = http_client or httpx.Client(timeout=30.0)
        self._max_attempts = max_attempts
        self._sleep = sleeper
        self._jitter = jitter
        self._endpoint = endpoint
        self._rate_limiter = rate_limiter

    def query(
        self,
        api_name: str,
        fields: Sequence[str],
        params: Mapping[str, object],
    ) -> QueryResult:
        requested = tuple(fields)
        for attempt in range(1, self._max_attempts + 1):
            if self._rate_limiter is not None:
                self._rate_limiter.acquire()
            try:
                response = self._http.post(
                    self._endpoint,
                    json={
                        "api_name": api_name,
                        "token": self._token.reveal(),
                        "params": dict(params),
                        "fields": ",".join(requested),
                    },
                )
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt == self._max_attempts:
                    raise TushareError(
                        f"DATA_PROVIDER_TEMPORARY_FAILURE: {api_name} request exhausted retries"
                    ) from error
                self._wait(attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self._max_attempts:
                    raise TushareError(
                        f"DATA_PROVIDER_TEMPORARY_FAILURE: {api_name} HTTP retries exhausted"
                    )
                self._wait(attempt)
                continue
            if response.status_code != 200:
                raise TushareError(
                    "DATA_PROVIDER_TEMPORARY_FAILURE: "
                    f"{api_name} returned HTTP {response.status_code}"
                )
            return self._decode(api_name, requested, response, attempt)
        raise AssertionError("unreachable")

    def query_all(
        self,
        api_name: str,
        fields: Sequence[str],
        params: Mapping[str, object],
        *,
        page_size: int,
        max_pages: int = 10_000,
    ) -> QueryResult:
        if page_size < 1 or max_pages < 1:
            raise ValueError("page_size and max_pages must be positive")
        base_params = dict(params)
        base_params.pop("offset", None)
        base_params.pop("limit", None)
        records: list[dict[str, Any]] = []
        page_numbers: list[int] = []
        returned_fields: tuple[str, ...] | None = None
        total_attempts = 0
        for page in range(max_pages):
            page_params = dict(base_params)
            page_params.update(offset=page * page_size, limit=page_size)
            result = self.query(api_name, fields, page_params)
            total_attempts += result.attempts
            if returned_fields is None:
                returned_fields = result.fields
            elif result.fields != returned_fields:
                raise TushareSchemaError(
                    f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} fields changed between pages"
                )
            records.extend(result.records)
            page_numbers.extend([page + 1] * len(result.records))
            if len(result.records) < page_size:
                return QueryResult(
                    returned_fields, tuple(records), total_attempts, tuple(page_numbers)
                )
        raise TushareError(
            f"DATA_PROVIDER_TEMPORARY_FAILURE: {api_name} pagination limit exhausted"
        )

    def _wait(self, attempt: int) -> None:
        self._sleep(float(2 ** (attempt - 1)) + max(0.0, self._jitter()))

    @staticmethod
    def _decode(
        api_name: str,
        requested: tuple[str, ...],
        response: httpx.Response,
        attempts: int,
    ) -> QueryResult:
        try:
            body = response.json()
        except ValueError as error:
            raise TushareSchemaError(
                f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} response is not JSON"
            ) from error
        code = body.get("code") if isinstance(body, dict) else None
        if code == 2002:
            raise TusharePermissionError(
                f"DATA_PROVIDER_PERMISSION_DENIED: {api_name} is not authorized"
            )
        if code != 0:
            raise TushareError(f"DATA_PROVIDER_TEMPORARY_FAILURE: {api_name} provider code {code}")
        data = body.get("data")
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("fields"), list)
            or not isinstance(data.get("items"), list)
        ):
            raise TushareSchemaError(f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} data envelope")
        returned_fields = tuple(data["fields"])
        missing = tuple(field for field in requested if field not in returned_fields)
        if missing:
            raise TushareSchemaError(
                f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} missing fields {','.join(missing)}"
            )
        records = []
        for row in data["items"]:
            if not isinstance(row, list) or len(row) != len(returned_fields):
                raise TushareSchemaError(f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} item width")
            records.append(dict(zip(returned_fields, row, strict=True)))
        return QueryResult(returned_fields, tuple(records), attempts)
