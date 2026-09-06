from __future__ import annotations

from types import SimpleNamespace

from xqatexp.providers.tushare.capability import CapabilityProbe, CapabilityStatus
from xqatexp.providers.tushare.client import TusharePermissionError, TushareSchemaError


class _Client:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, tuple[str, ...], dict[str, object]]] = []

    def query(self, api_name, fields, params):
        self.calls.append((api_name, tuple(fields), dict(params)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_successful_empty_stock_st_is_distinct_from_permission_denial() -> None:
    empty = _Client(SimpleNamespace(fields=("ts_code",), records=(), attempts=1))
    result = CapabilityProbe(empty).run(("stock_st_status",), trade_date="20260904")
    assert result[0].status is CapabilityStatus.EMPTY_BUT_AUTHORIZED
    assert result[0].row_count == 0

    denied = _Client(TusharePermissionError("DATA_PROVIDER_PERMISSION_DENIED: stock_st"))
    result = CapabilityProbe(denied).run(("stock_st_status",), trade_date="20260904")
    assert result[0].status is CapabilityStatus.PERMISSION_DENIED
    assert "token" not in repr(result).lower()


def test_capability_classifies_schema_mismatch_without_cross_dataset_inference() -> None:
    client = _Client(TushareSchemaError("DATA_PROVIDER_SCHEMA_MISMATCH: daily missing field"))
    results = CapabilityProbe(client).run(("stock_daily",), trade_date="20260904")
    assert len(results) == 1
    assert results[0].dataset_id == "stock_daily"
    assert results[0].status is CapabilityStatus.SCHEMA_MISMATCH


def test_capability_uses_minimal_registered_request() -> None:
    client = _Client(
        SimpleNamespace(fields=("ts_code", "trade_date", "adj_factor"), records=({},), attempts=1)
    )
    result = CapabilityProbe(client).run(("fund_adj_factor",), trade_date="20260904")
    assert result[0].status is CapabilityStatus.AVAILABLE
    assert client.calls == [
        (
            "fund_adj",
            ("ts_code", "trade_date", "adj_factor"),
            {"ts_code": "510300.SH", "start_date": "20260904", "end_date": "20260904"},
        )
    ]


def test_dividend_probe_uses_only_an_officially_supported_selector() -> None:
    client = _Client(SimpleNamespace(fields=("ts_code",), records=(), attempts=1))
    CapabilityProbe(client).run(("dividend",), trade_date="20260904")
    assert client.calls[0][2] == {"ts_code": "600000.SH"}
