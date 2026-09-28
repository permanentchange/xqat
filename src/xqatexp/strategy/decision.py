from __future__ import annotations

import hashlib
import json
from datetime import date


def stable_decision_id(
    strategy_id: str,
    strategy_version: str,
    decision_date: date,
    effective_from: date,
    decision_kind: str,
) -> str:
    payload = json.dumps(
        {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "decision_date": decision_date.isoformat(),
            "effective_from": effective_from.isoformat(),
            "decision_kind": decision_kind,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_instruction_id(decision_id: str, security_id: str, side: str) -> str:
    payload = json.dumps(
        {
            "decision_id": decision_id,
            "security_id": security_id,
            "side": side,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_intent_id(
    decision_id: str,
    index: int,
    security_id: str,
    side: str,
) -> str:
    payload = json.dumps(
        {
            "decision_id": decision_id,
            "index": index,
            "security_id": security_id,
            "side": side,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
