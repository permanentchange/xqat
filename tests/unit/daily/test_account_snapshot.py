from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from xqatexp.daily.account_snapshot import parse_account_snapshot
from xqatexp.domain.enums import PositionCompleteness


def _write(path: Path, completeness: str, positions: list[dict[str, object]]) -> Path:
    value = {
        "schema_version": "1.0",
        "as_of": "2026-09-04T18:00:00+08:00",
        "currency": "CNY",
        "account_scope": "STRATEGY_MANAGED",
        "scope_completeness": "COMPLETE",
        "positions_completeness": completeness,
        "available_cash": 100000,
        "managed_total_assets": 100000,
        "excluded_asset_value": 0,
        "source_note": "test fixture",
        "positions": positions,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_confirmed_empty_is_distinct_from_unknown(tmp_path: Path) -> None:
    empty = parse_account_snapshot(
        _write(tmp_path / "empty.json", "COMPLETE", []),
        run_started_at=datetime.fromisoformat("2026-09-04T19:00:00+08:00"),
    )
    unknown = parse_account_snapshot(
        _write(tmp_path / "unknown.json", "UNKNOWN", []),
        run_started_at=datetime.fromisoformat("2026-09-04T19:00:00+08:00"),
    )
    assert empty.positions_completeness is PositionCompleteness.COMPLETE
    assert unknown.positions_completeness is PositionCompleteness.UNKNOWN


def test_duplicate_security_and_future_snapshot_are_rejected(tmp_path: Path) -> None:
    position = {
        "security_id": "600000.SH",
        "quantity": 100,
        "sellable_quantity": 100,
        "market_value": 1000,
        "reference_price": 10,
        "reference_price_date": "2026-09-04",
    }
    path = _write(tmp_path / "duplicate.json", "COMPLETE", [position, position])
    with pytest.raises(ValueError, match="CONFIG_SCHEMA_INVALID"):
        parse_account_snapshot(
            path,
            run_started_at=datetime.fromisoformat("2026-09-04T17:00:00+08:00"),
        )
