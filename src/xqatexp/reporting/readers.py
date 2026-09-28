from __future__ import annotations

import json
from datetime import date
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
from xqatexp.domain.enums import AssetType, TargetTransition


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
    version = str(value["schema_version"])
    legacy = version.split(".", 1)[0] == "1"

    positions = tuple(
        TargetPosition(
            security_id=item["security_id"],
            asset_type=AssetType(item["asset_type"]),
            target_weight=Decimal(str(item["target_weight"])),
            transition=TargetTransition(item["transition"]) if item["transition"] else None,
            explanation_codes=tuple(item["explanation_codes"]),
            planning_priority=(item.get("rank") if legacy else item.get("planning_priority")),
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
    return TargetPortfolio(
        value["strategy_id"],
        value["strategy_version"],
        date.fromisoformat(value["decision_date"]),
        date.fromisoformat(value["effective_from"]),
        positions,
        transitions,
        Decimal(str(value["cash_weight"])),
        explanations,
    )
