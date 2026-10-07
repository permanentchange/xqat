from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Event, Lock
from typing import Any

import httpx

from xqatexp.security import SecretValue


class TushareError(RuntimeError):
    """A redacted provider failure with machine-readable diagnostic metadata."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int = 0,
        http_status: int | None = None,
        provider_code: object = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.http_status = http_status
        self.provider_code = provider_code if isinstance(provider_code, int) else None


class TusharePermissionError(TushareError):
    """The provider rejected access to a requested interface."""


class TushareAuthenticationError(TusharePermissionError):
    """The provider rejected the account credential."""


class TushareSchemaError(TushareError):
    """The provider response violated the response or pagination contract."""


class TushareParameterError(TushareError):
    """A permanent request error."""


class TushareCancelledError(TushareError):
    """The shared runtime was cancelled."""


@dataclass(frozen=True, slots=True)
class QueryResult:
    fields: tuple[str, ...]
    records: tuple[dict[str, Any], ...]
    attempts: int
    page_numbers: tuple[int, ...] = ()


def _pause(seconds: float, sleeper: Callable[[float], None], cancelled: Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise TushareCancelledError("DATA_FETCH_CANCELLED: runtime cancelled")
    if cancelled is not None and sleeper is time.sleep:
        if cancelled.wait(seconds):
            raise TushareCancelledError("DATA_FETCH_CANCELLED: runtime cancelled")
    else:
        sleeper(seconds)


class TokenBucket:
    """A process-local, thread-safe limiter with cancellable shared cooldown."""

    def __init__(
        self,
        *,
        calls_per_minute: float,
        capacity: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        cancelled: Event | None = None,
    ) -> None:
        if calls_per_minute <= 0 or capacity < 1:
            raise ValueError("rate and capacity must be positive")
        self._rate = calls_per_minute / 60.0
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock, self._sleep, self._cancelled = clock, sleeper, cancelled
        self._updated_at = clock()
        self._cooldown_until = self._updated_at
        self._lock = Lock()

    def defer(self, seconds: float) -> None:
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, self._clock() + seconds)

    def acquire(self) -> None:
        self.acquire_many((self,))

    @staticmethod
    def acquire_many(gates: Sequence[TokenBucket]) -> None:
        """Consume global/API permits together, so permits cannot accumulate in workers."""
        gates = tuple(sorted(set(gates), key=id))
        if not gates:
            return
        while True:
            for gate in gates:
                if gate._cancelled is not None and gate._cancelled.is_set():
                    raise TushareCancelledError("DATA_FETCH_CANCELLED: runtime cancelled")
            with ExitStack() as locks:
                for gate in gates:
                    locks.enter_context(gate._lock)
                delay = 0.0
                for gate in gates:
                    now = gate._clock()
                    gate._tokens = min(
                        gate._capacity, gate._tokens + max(0.0, now - gate._updated_at) * gate._rate
                    )
                    gate._updated_at = now
                    delay = max(
                        delay, gate._cooldown_until - now, (1.0 - gate._tokens) / gate._rate
                    )
                if delay <= 0:
                    for gate in gates:
                        gate._tokens -= 1.0
                    return
            _pause(max(delay, 1e-9), gates[0]._sleep, gates[0]._cancelled)


class TushareClient:
    def __init__(
        self,
        token: SecretValue,
        *,
        http_client: httpx.Client | None = None,
        max_attempts: int = 5,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        endpoint: str = "https://api.tushare.pro",
        rate_limiter: TokenBucket | None = None,
        api_limiters: Mapping[str, TokenBucket] | None = None,
        cancelled: Event | None = None,
    ) -> None:
        if not 1 <= max_attempts <= 8:
            raise ValueError("max_attempts must be in [1, 8]")
        self._token = token
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=30.0)
        self._max_attempts, self._sleep, self._jitter = max_attempts, sleeper, jitter
        self._endpoint, self._rate_limiter = endpoint, rate_limiter
        self._api_limiters = dict(api_limiters or {})
        self._cancelled = cancelled
        self._lock = Lock()
        self._verified_apis: set[str] = set()
        self._probe_lock = Lock()
        self._stats: dict[str, float] = dict(
            requests=0,
            retries=0,
            pages=0,
            rate_wait_seconds=0,
            network_seconds=0,
            retry_wait_seconds=0,
            publish_seconds=0,
        )

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> TushareClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _metric(self, name: str, value: float = 1) -> None:
        with self._lock:
            self._stats[name] += value

    def metrics(self) -> dict[str, float]:
        with self._lock:
            return dict(self._stats)

    def query(
        self, api_name: str, fields: Sequence[str], params: Mapping[str, object]
    ) -> QueryResult:
        requested = tuple(fields)
        limiter = self._api_limiters.get(api_name)
        for attempt in range(1, self._max_attempts + 1):
            if self._cancelled is not None and self._cancelled.is_set():
                raise TushareCancelledError("DATA_FETCH_CANCELLED: runtime cancelled")
            started = time.monotonic()
            TokenBucket.acquire_many(
                tuple(gate for gate in (limiter, self._rate_limiter) if gate is not None)
            )
            self._metric("rate_wait_seconds", time.monotonic() - started)
            self._metric("requests")
            if attempt > 1:
                self._metric("retries")
            started = time.monotonic()
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
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                self._metric("network_seconds", time.monotonic() - started)
                if attempt == self._max_attempts:
                    raise TushareError(
                        f"DATA_PROVIDER_TEMPORARY_FAILURE: {api_name} network retries exhausted",
                        attempts=attempt,
                    ) from None
                self._wait(attempt)
                continue
            else:
                self._metric("network_seconds", time.monotonic() - started)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self._max_attempts:
                    raise TushareError(
                        f"DATA_PROVIDER_TEMPORARY_FAILURE: {api_name} HTTP retries exhausted",
                        attempts=attempt,
                        http_status=response.status_code,
                    )
                delay = (
                    self._cooldown(limiter or self._rate_limiter, response)
                    if (response.status_code == 429)
                    else 0.0
                )
                self._wait(attempt, minimum=delay)
                continue
            if response.status_code == 403:
                raise TusharePermissionError(
                    f"DATA_PROVIDER_PERMISSION_DENIED: {api_name} HTTP permission",
                    attempts=attempt,
                    http_status=403,
                )
            if response.status_code == 401:
                raise TushareAuthenticationError(
                    f"DATA_PROVIDER_AUTHENTICATION_FAILED: {api_name} HTTP authentication",
                    attempts=attempt,
                    http_status=response.status_code,
                )
            if response.status_code != 200:
                raise TushareParameterError(
                    f"DATA_PROVIDER_REQUEST_INVALID: {api_name} HTTP {response.status_code}",
                    attempts=attempt,
                    http_status=response.status_code,
                )
            try:
                body = response.json()
            except ValueError:
                raise TushareSchemaError(
                    f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} response is not JSON",
                    attempts=attempt,
                ) from None
            code = body.get("code") if isinstance(body, dict) else None
            message = str(body.get("msg", "")) if isinstance(body, dict) else ""
            # Inspect provider messages only for classification; never retain their text.
            if code != 0 and any(
                word in message for word in ("每分钟", "频次", "频率", "rate limit")
            ):
                if attempt == self._max_attempts:
                    raise TushareError(
                        f"DATA_PROVIDER_RATE_LIMITED: {api_name} retries exhausted",
                        attempts=attempt,
                        provider_code=code,
                    )
                delay = self._cooldown(limiter or self._rate_limiter, response)
                self._wait(attempt, minimum=delay)
                continue
            if code != 0 and "token" in message.lower():
                raise TushareAuthenticationError(
                    f"DATA_PROVIDER_AUTHENTICATION_FAILED: {api_name} credential rejected",
                    attempts=attempt,
                    provider_code=code,
                )
            try:
                return self._decode(api_name, requested, response, attempt)
            except TushareError as error:
                if error.attempts == 0:
                    error.attempts = attempt
                raise
        raise AssertionError("unreachable")

    def _cooldown(self, limiter: TokenBucket | None, response: httpx.Response) -> float:
        value = response.headers.get("Retry-After")
        seconds = 60.0 if limiter is not None else 1.0
        if value is not None:
            try:
                seconds = max(0.0, float(value))
            except ValueError:
                try:
                    seconds = max(
                        0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
                    )
                except (ValueError, TypeError, OverflowError):
                    seconds = 60.0
        if limiter is not None:
            limiter.defer(seconds)
            return 0.0
        return seconds

    def verify_pagination(
        self, api_name: str, fields: Sequence[str], params: Mapping[str, object]
    ) -> int:
        """VIP is enabled only after observing limit=1 and offset advancement."""
        with self._probe_lock:
            if api_name in self._verified_apis:
                return 0
            first = self.query(api_name, fields, {**params, "offset": 0, "limit": 1})
            second = self.query(api_name, fields, {**params, "offset": 1, "limit": 1})
            if (
                len(first.records) != 1
                or len(second.records) != 1
                or first.records == second.records
            ):
                raise TushareSchemaError(
                    f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} pagination not confirmed",
                    attempts=first.attempts + second.attempts,
                )
            self._verified_apis.add(api_name)
            return first.attempts + second.attempts

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
        base_params = {
            key: value for key, value in params.items() if key not in ("offset", "limit")
        }
        records: list[dict[str, Any]] = []
        page_numbers: list[int] = []
        returned_fields: tuple[str, ...] | None = None
        total_attempts = 0
        seen: set[str] = set()
        for page in range(max_pages):
            try:
                result = self.query(
                    api_name, fields, {**base_params, "offset": len(records), "limit": page_size}
                )
            except TushareError as error:
                error.attempts += total_attempts
                raise
            self._metric("pages")
            total_attempts += result.attempts
            if returned_fields is None:
                returned_fields = result.fields
            elif result.fields != returned_fields:
                raise TushareSchemaError(
                    f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} fields changed between pages",
                    attempts=total_attempts,
                )
            if not result.records:
                return QueryResult(
                    returned_fields, tuple(records), total_attempts, tuple(page_numbers)
                )
            for row in result.records:
                identity = json.dumps(row, sort_keys=True, allow_nan=False, separators=(",", ":"))
                if identity in seen:
                    raise TushareSchemaError(
                        f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} duplicate or nonadvancing page",
                        attempts=total_attempts,
                    )
                seen.add(identity)
            records.extend(result.records)
            page_numbers.extend([page + 1] * len(result.records))
            # Require an empty terminal page: a short page may be an undocumented server cap.
        raise TushareSchemaError(
            f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} pagination limit exhausted",
            attempts=total_attempts,
        )

    def _wait(self, attempt: int, *, minimum: float = 0.0) -> None:
        started = time.monotonic()
        _pause(
            max(minimum, min(30.0, float(2 ** (attempt - 1)) + max(0.0, self._jitter()))),
            self._sleep,
            self._cancelled,
        )
        self._metric("retry_wait_seconds", time.monotonic() - started)

    @staticmethod
    def _decode(
        api_name: str, requested: tuple[str, ...], response: httpx.Response, attempts: int
    ) -> QueryResult:
        try:
            body = response.json()
        except ValueError:
            raise TushareSchemaError(
                f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} response is not JSON"
            ) from None
        code = body.get("code") if isinstance(body, dict) else None
        if code in (2002, -2002):
            raise TusharePermissionError(
                f"DATA_PROVIDER_PERMISSION_DENIED: {api_name} is not authorized",
                attempts=attempts,
                provider_code=code,
            )
        if code != 0:
            raise TushareParameterError(
                f"DATA_PROVIDER_REQUEST_INVALID: {api_name} provider rejected request",
                attempts=attempts,
                provider_code=code,
            )
        data = body.get("data")
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("fields"), list)
            or not isinstance(data.get("items"), list)
        ):
            raise TushareSchemaError(f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} data envelope")
        returned_fields = tuple(data["fields"])
        if (
            not all(isinstance(field, str) for field in returned_fields)
            or len(set(returned_fields)) != len(returned_fields)
            or not set(requested).issubset(returned_fields)
        ):
            raise TushareSchemaError(f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} missing fields")
        records = []
        for row in data["items"]:
            if not isinstance(row, list) or len(row) != len(returned_fields):
                raise TushareSchemaError(f"DATA_PROVIDER_SCHEMA_MISMATCH: {api_name} item width")
            records.append(dict(zip(returned_fields, row, strict=True)))
        return QueryResult(returned_fields, tuple(records), attempts)
