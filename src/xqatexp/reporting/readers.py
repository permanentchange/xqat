from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.domain.contracts import (
    Explanation,
    TargetPortfolio,
    TargetPosition,
    TargetTransitionRecord,
)
from xqatexp.domain.enums import AssetType, MarketRegime, TargetTransition


def load_target(path: Path) -> TargetPortfolio:
    source = path
    if path.is_dir():
        opened = ArtifactReader().open(path)
        if opened.manifest["artifact_type"] not in {
            "DAILY_TARGET_RESULT",
            "DAILY_ADVICE_RESULT",
        }:
            raise ValueError("ARTIFACT_SCHEMA_INCOMPATIBLE: target result required")
        source = opened.path / "target_portfolio.json"
    value = json.loads(source.read_text(encoding="utf-8"), parse_float=Decimal)
    SchemaRegistry().validate_json("target_portfolio", value)
    positions = tuple(
        TargetPosition(
            item["security_id"],
            AssetType(item["asset_type"]),
            Decimal(str(item["target_weight"])),
            item["rank"],
            item["score"],
            TargetTransition(item["transition"]) if item["transition"] else None,
            tuple(item["explanation_codes"]),
            item["holding_age_weeks"],
        )
        for item in value["positions"]
    )
    transitions = tuple(
        TargetTransitionRecord(
            item["security_id"],
            Decimal(str(item["previous_weight"])),
            Decimal(str(item["current_weight"])),
            TargetTransition(item["transition"]),
            tuple(item["explanation_codes"]),
        )
        for item in value["transition_records"]
    )
    explanations = tuple(
        Explanation(item["code"], item["message"], item["values"]) for item in value["explanations"]
    )
    from datetime import date

    return TargetPortfolio(
        value["strategy_id"],
        value["strategy_version"],
        date.fromisoformat(value["decision_date"]),
        date.fromisoformat(value["effective_from"]),
        MarketRegime(value["market_regime"]),
        Decimal(str(value["theoretical_drawdown"])),
        value["drawdown_window_trade_days"],
        value["drawdown_observations"],
        value["drawdown_overlay_level"],
        positions,
        transitions,
        Decimal(str(value["cash_weight"])),
        explanations,
    )
