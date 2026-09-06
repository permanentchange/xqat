from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from xqatexp.providers.tushare.client import (
    QueryResult,
    TushareError,
    TusharePermissionError,
    TushareSchemaError,
)
from xqatexp.providers.tushare.registry import DatasetSpec, get_dataset


class CapabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    EMPTY_BUT_AUTHORIZED = "EMPTY_BUT_AUTHORIZED"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    dataset_id: str
    api_name: str
    status: CapabilityStatus
    returned_fields: tuple[str, ...]
    row_count: int | None
    evidence_code: str | None = None


class QueryClient(Protocol):
    def query(
        self, api_name: str, fields: Sequence[str], params: dict[str, object]
    ) -> QueryResult: ...


class CapabilityProbe:
    def __init__(self, client: QueryClient, *, etf_id: str = "510300.SH") -> None:
        self._client = client
        self._etf_id = etf_id

    def run(self, dataset_ids: Sequence[str], *, trade_date: str) -> tuple[CapabilityResult, ...]:
        results = []
        for dataset_id in dataset_ids:
            spec = get_dataset(dataset_id)
            try:
                response = self._client.query(
                    spec.api_name, spec.fields, self._minimal_params(spec, trade_date)
                )
                status = (
                    CapabilityStatus.AVAILABLE
                    if response.records
                    else CapabilityStatus.EMPTY_BUT_AUTHORIZED
                )
                results.append(
                    CapabilityResult(
                        dataset_id,
                        spec.api_name,
                        status,
                        response.fields,
                        len(response.records),
                    )
                )
            except TusharePermissionError:
                results.append(self._failure(spec, CapabilityStatus.PERMISSION_DENIED))
            except TushareSchemaError:
                results.append(self._failure(spec, CapabilityStatus.SCHEMA_MISMATCH))
            except TushareError:
                results.append(self._failure(spec, CapabilityStatus.TRANSIENT_FAILURE))
        return tuple(results)

    @staticmethod
    def _failure(spec: DatasetSpec, status: CapabilityStatus) -> CapabilityResult:
        code = {
            CapabilityStatus.PERMISSION_DENIED: "DATA_PROVIDER_PERMISSION_DENIED",
            CapabilityStatus.SCHEMA_MISMATCH: "DATA_PROVIDER_SCHEMA_MISMATCH",
            CapabilityStatus.TRANSIENT_FAILURE: "DATA_PROVIDER_TEMPORARY_FAILURE",
        }[status]
        return CapabilityResult(spec.dataset_id, spec.api_name, status, (), None, code)

    def _minimal_params(self, spec: DatasetSpec, trade_date: str) -> dict[str, object]:
        params: dict[str, object] = dict(spec.fixed_params)
        if spec.dataset_id == "stock_basic":
            params.update(exchange="SSE", list_status="L")
        elif spec.dataset_id == "trade_calendar":
            params.update(start_date=trade_date, end_date=trade_date)
        elif spec.dataset_id in {
            "stock_daily",
            "stock_adj_factor",
            "stock_daily_basic",
            "stock_suspend",
            "stock_price_limit",
            "stock_st_status",
        }:
            params["trade_date"] = trade_date
        elif spec.dataset_id == "fund_basic":
            params["status"] = "L"
        elif spec.dataset_id in {"fund_daily", "fund_adj_factor"}:
            params.update(ts_code=self._etf_id, start_date=trade_date, end_date=trade_date)
        elif spec.dataset_id == "index_daily":
            params.update(start_date=trade_date, end_date=trade_date)
        elif spec.dataset_id in {"income", "fina_indicator"}:
            params.update(ts_code="600000.SH", start_date=trade_date, end_date=trade_date)
        elif spec.dataset_id == "dividend":
            params["ts_code"] = "600000.SH"
        return params
