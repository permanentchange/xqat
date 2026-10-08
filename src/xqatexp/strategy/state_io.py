from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.state import StrategyPositionState, StrategyStateSnapshot


def strategy_state_value(state: StrategyStateSnapshot) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "strategy_id": state.strategy_id,
        "strategy_version": state.strategy_version,
        "initial_capital": state.initial_capital,
        "as_of": state.as_of.isoformat() if state.as_of is not None else None,
        "positions": [
            {
                "security_id": item.security_id,
                "quantity": item.quantity,
                "remaining_cost_basis": item.remaining_cost_basis,
                "last_trade_side": item.last_trade_side,
                "last_trade_date": (
                    item.last_trade_date.isoformat() if item.last_trade_date is not None else None
                ),
                "last_trade_quantity": item.last_trade_quantity,
                "last_trade_price": item.last_trade_price,
                "last_buy_price": item.last_buy_price,
                "cumulative_buy_notional": item.cumulative_buy_notional,
                "exit_base_quantity": item.exit_base_quantity,
                "exit_sold_quantity": item.exit_sold_quantity,
            }
            for item in state.positions
        ],
    }


def load_strategy_state(path: Path) -> StrategyStateSnapshot:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
        SchemaRegistry().validate_json("strategy_state", value)
    except Exception as error:
        raise ValueError(f"CONFIG_SCHEMA_INVALID: invalid strategy state: {error}") from error
    positions = tuple(
        StrategyPositionState(
            security_id=str(item["security_id"]),
            quantity=int(item["quantity"]),
            remaining_cost_basis=Decimal(str(item["remaining_cost_basis"])),
            last_trade_side=(
                None if item["last_trade_side"] is None else OrderSide(str(item["last_trade_side"]))
            ),
            last_trade_date=(
                None
                if item["last_trade_date"] is None
                else date.fromisoformat(item["last_trade_date"])
            ),
            last_trade_quantity=int(item["last_trade_quantity"]),
            last_trade_price=(
                None if item["last_trade_price"] is None else Decimal(str(item["last_trade_price"]))
            ),
            last_buy_price=(
                None if item["last_buy_price"] is None else Decimal(str(item["last_buy_price"]))
            ),
            cumulative_buy_notional=Decimal(str(item["cumulative_buy_notional"])),
            exit_base_quantity=(
                None
                if item.get("exit_base_quantity") is None
                else Decimal(str(item["exit_base_quantity"]))
            ),
            exit_sold_quantity=Decimal(str(item.get("exit_sold_quantity", "0"))),
        )
        for item in value["positions"]
    )
    ids = [item.security_id for item in positions]
    if len(ids) != len(set(ids)):
        raise ValueError("CONFIG_SCHEMA_INVALID: duplicate strategy state security_id")
    return StrategyStateSnapshot(
        schema_version=str(value["schema_version"]),
        strategy_id=str(value["strategy_id"]),
        strategy_version=str(value["strategy_version"]),
        initial_capital=Decimal(str(value["initial_capital"])),
        as_of=None if value["as_of"] is None else date.fromisoformat(value["as_of"]),
        positions=tuple(sorted(positions, key=lambda item: item.security_id)),
    )
