from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.domain.contracts import AccountPosition, AccountSnapshot
from xqatexp.domain.enums import (
    AccountScope,
    AccountScopeCompleteness,
    PositionCompleteness,
)


def _decimal(value: object | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _date(value: object | None) -> date | None:
    return None if value is None else date.fromisoformat(str(value))


def parse_account_snapshot(path: Path, *, run_started_at: datetime) -> AccountSnapshot:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
        SchemaRegistry().validate_json("account_snapshot", value)
        as_of = datetime.fromisoformat(str(value["as_of"]))
    except Exception as error:
        raise ValueError(f"CONFIG_SCHEMA_INVALID: invalid account snapshot: {error}") from error
    if as_of.tzinfo is None or run_started_at.tzinfo is None or as_of > run_started_at:
        raise ValueError("CONFIG_SCHEMA_INVALID: account as_of is future or lacks timezone")
    raw_positions = value["positions"]
    ids = [str(item["security_id"]) for item in raw_positions]
    if len(ids) != len(set(ids)):
        raise ValueError("CONFIG_SCHEMA_INVALID: duplicate account security_id")
    positions = []
    for item in raw_positions:
        quantity = int(item["quantity"])
        sellable = item["sellable_quantity"]
        if sellable is not None and int(sellable) > quantity:
            raise ValueError("CONFIG_SCHEMA_INVALID: sellable_quantity exceeds quantity")
        positions.append(
            AccountPosition(
                str(item["security_id"]),
                quantity,
                None if sellable is None else int(sellable),
                _decimal(item["market_value"]),
                _decimal(item["reference_price"]),
                _date(item["reference_price_date"]),
            )
        )
    return AccountSnapshot(
        str(value["schema_version"]),
        as_of,
        str(value["currency"]),
        AccountScope(str(value["account_scope"])),
        AccountScopeCompleteness(str(value["scope_completeness"])),
        PositionCompleteness(str(value["positions_completeness"])),
        _decimal(value["available_cash"]),
        _decimal(value["managed_total_assets"]),
        _decimal(value["excluded_asset_value"]),
        str(value["source_note"]),
        tuple(positions),
    )
