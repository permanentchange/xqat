from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

import httpx

from xqatexp.providers.tushare.client import TokenBucket, TushareClient
from xqatexp.providers.tushare.registry import API_PAGE_SIZES
from xqatexp.security import SecretValue


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    calls_per_minute: float = 200
    workers: int = 4
    max_attempts: int = 5
    timeout_seconds: float = 30
    api_limits: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.calls_per_minute)
            or self.calls_per_minute <= 0
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or not 1 <= self.workers <= 8
            or not 1 <= self.max_attempts <= 8
            or any(not math.isfinite(rate) or rate <= 0 for rate in self.api_limits.values())
        ):
            raise ValueError("CONFIG_VALUE_INVALID: provider rate/workers/retries/timeout")

    @classmethod
    def load(cls, path: Path | None) -> ProviderConfig:
        if path is None:
            return cls()
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        allowed = {"calls_per_minute", "workers", "max_attempts", "timeout_seconds", "api_limits"}
        if set(value) - allowed or re.search(
            r"token|secret|password|credential|authorization", " ".join(value), re.IGNORECASE
        ):
            raise ValueError("CONFIG_SCHEMA_INVALID: unknown or secret provider settings")
        try:
            for name in ("workers", "max_attempts"):
                if name in value and type(value[name]) is not int:
                    raise ValueError(name)
            for name in ("calls_per_minute", "timeout_seconds"):
                if name in value and type(value[name]) not in (int, float):
                    raise ValueError(name)
            limits = value.get("api_limits", {})
            if not isinstance(limits, dict):
                raise ValueError("api_limits")
            if set(limits) - set(API_PAGE_SIZES):
                raise ValueError("unknown API limit")
            if any(type(rate) not in (int, float) for rate in limits.values()):
                raise ValueError("api_limits")
            return cls(**value)
        except (TypeError, ValueError) as error:
            raise ValueError("CONFIG_SCHEMA_INVALID: invalid provider settings") from error


class ProviderRuntime:
    def __init__(self, token: SecretValue, config: ProviderConfig) -> None:
        self.config = config
        self.cancelled = Event()
        self._http = httpx.Client(
            timeout=config.timeout_seconds,
            limits=httpx.Limits(
                max_connections=config.workers,
                max_keepalive_connections=config.workers,
            ),
        )
        self.client = TushareClient(
            token,
            http_client=self._http,
            max_attempts=config.max_attempts,
            rate_limiter=TokenBucket(
                calls_per_minute=config.calls_per_minute, cancelled=self.cancelled
            ),
            api_limiters={
                api: TokenBucket(calls_per_minute=rate, cancelled=self.cancelled)
                for api, rate in config.api_limits.items()
            },
            cancelled=self.cancelled,
        )

    def __enter__(self) -> ProviderRuntime:
        return self

    def __exit__(self, *_args: object) -> None:
        self.cancelled.set()
        self._http.close()
