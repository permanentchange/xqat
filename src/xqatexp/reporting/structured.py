from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from pathlib import Path

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.domain.contracts import (
    AccountSnapshot,
    ResolvedRunContext,
    TargetPortfolio,
    TradeAdvice,
)
from xqatexp.domain.issues import Issue


def resolved_context_value(context: ResolvedRunContext) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "mode": context.mode,
        "strategy_id": context.strategy_id,
        "strategy_version": context.strategy_version,
        "parameters": dict(context.parameters),
        "research_input": {
            "alias": "research",
            "manifest_sha256": context.research_artifact_sha256,
        },
        "custom_factor_inputs": [
            {"alias": f"custom-factor-{index}", "sha256": item.sha256}
            for index, item in enumerate(context.custom_factor_inputs, start=1)
        ],
        "decision_date": (
            context.decision_date.isoformat() if context.decision_date is not None else None
        ),
        "start_date": context.start_date.isoformat() if context.start_date is not None else None,
        "end_date": context.end_date.isoformat() if context.end_date is not None else None,
        "account_input": (
            {"alias": "account", "provided": True}
            if context.account_snapshot_path is not None
            else None
        ),
        "previous_target_input": (
            {"alias": "previous-target", "provided": True}
            if context.previous_target_path is not None
            else None
        ),
        "output_alias": context.output_path.name,
        "execution_assumptions": dict(context.execution_assumptions),
    }


def target_value(target: TargetPortfolio) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "strategy_id": target.strategy_id,
        "strategy_version": target.strategy_version,
        "decision_date": target.decision_date.isoformat(),
        "effective_from": target.effective_from.isoformat(),
        "market_regime": target.market_regime,
        "theoretical_drawdown": target.theoretical_drawdown,
        "drawdown_window_trade_days": target.drawdown_window_trade_days,
        "drawdown_observations": target.drawdown_observations,
        "drawdown_overlay_level": target.drawdown_overlay_level,
        "positions": [
            {
                "security_id": item.security_id,
                "asset_type": item.asset_type,
                "target_weight": item.target_weight,
                "rank": item.rank,
                "score": item.score,
                "transition": item.transition,
                "explanation_codes": list(item.explanation_codes),
                "holding_age_weeks": item.holding_age_weeks,
            }
            for item in target.positions
        ],
        "transition_records": [
            {
                "security_id": item.security_id,
                "previous_weight": item.previous_weight,
                "current_weight": item.current_weight,
                "transition": item.transition,
                "explanation_codes": list(item.explanation_codes),
            }
            for item in target.transition_records
        ],
        "cash_weight": target.cash_weight,
        "explanations": [
            {"code": item.code, "message": item.message, "values": dict(item.values)}
            for item in target.explanations
        ],
    }


def issue_value(issue: Issue) -> dict[str, object]:
    return {
        "code": issue.code,
        "severity": issue.severity,
        "stage": issue.stage,
        "scope": issue.scope,
        "decision_date": issue.decision_date,
        "security_id": issue.security_id,
        "field": issue.field,
        "message": issue.message,
        "evidence": dict(issue.evidence),
        "suggested_action": issue.suggested_action,
    }


def issues_value(issues: Sequence[Issue]) -> dict[str, object]:
    return {"schema_version": "1.0", "issues": [issue_value(item) for item in issues]}


def target_positions_csv(target: TargetPortfolio) -> bytes:
    stream = io.StringIO(newline="")
    columns = (
        "decision_date",
        "effective_from",
        "security_id",
        "asset_type",
        "target_weight",
        "rank",
        "score",
        "holding_age_weeks",
        "transition",
        "explanation_codes",
    )
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for item in target.positions:
        writer.writerow(
            {
                "decision_date": target.decision_date.isoformat(),
                "effective_from": target.effective_from.isoformat(),
                "security_id": item.security_id,
                "asset_type": item.asset_type.value,
                "target_weight": format(item.target_weight, "f"),
                "rank": "" if item.rank is None else item.rank,
                "score": "" if item.score is None else repr(item.score),
                "holding_age_weeks": (
                    "" if item.holding_age_weeks is None else item.holding_age_weeks
                ),
                "transition": "" if item.transition is None else item.transition.value,
                "explanation_codes": ";".join(item.explanation_codes),
            }
        )
    return stream.getvalue().encode("utf-8")


def advice_value(advice: TradeAdvice, account: AccountSnapshot | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "decision_date": advice.decision_date.isoformat(),
        "effective_from": advice.effective_from.isoformat(),
        "account_as_of": account.as_of.isoformat() if account is not None else None,
        "account_scope": account.account_scope if account is not None else None,
        "scope_completeness": account.scope_completeness if account is not None else None,
        "positions_completeness": (account.positions_completeness if account is not None else None),
        "managed_total_assets": account.managed_total_assets if account is not None else None,
        "available_cash": account.available_cash if account is not None else None,
        "items": [
            {
                "security_id": item.security_id,
                "action": item.action,
                "current_quantity": item.current_quantity,
                "target_weight": item.target_weight,
                "target_amount": item.target_amount,
                "theoretical_target_quantity": item.theoretical_target_quantity,
                "suggested_quantity": item.suggested_quantity,
                "max_confirmed_sell_quantity": item.max_confirmed_sell_quantity,
                "unresolved_quantity": item.unresolved_quantity,
                "reference_price": item.reference_price,
                "reference_price_date": (
                    item.reference_price_date.isoformat()
                    if item.reference_price_date is not None
                    else None
                ),
                "reason_codes": list(item.reason_codes),
                "limitations": list(item.limitations),
            }
            for item in advice.items
        ],
        "issues": [issue_value(item) for item in advice.issues],
        "limitations": list(advice.limitations),
        "non_order_disclaimer": advice.non_order_disclaimer,
    }


def advice_csv(advice: TradeAdvice) -> bytes:
    stream = io.StringIO(newline="")
    columns = (
        "decision_date",
        "effective_from",
        "security_id",
        "action",
        "current_quantity",
        "target_weight",
        "target_amount",
        "theoretical_target_quantity",
        "suggested_quantity",
        "max_confirmed_sell_quantity",
        "unresolved_quantity",
        "reference_price",
        "reference_price_date",
        "reason_codes",
        "limitations",
    )
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for item in advice.items:
        writer.writerow(
            {
                "decision_date": advice.decision_date.isoformat(),
                "effective_from": advice.effective_from.isoformat(),
                "security_id": item.security_id,
                "action": item.action.value,
                "current_quantity": "" if item.current_quantity is None else item.current_quantity,
                "target_weight": format(item.target_weight, "f"),
                "target_amount": (
                    "" if item.target_amount is None else format(item.target_amount, "f")
                ),
                "theoretical_target_quantity": (
                    ""
                    if item.theoretical_target_quantity is None
                    else item.theoretical_target_quantity
                ),
                "suggested_quantity": (
                    "" if item.suggested_quantity is None else item.suggested_quantity
                ),
                "max_confirmed_sell_quantity": (
                    ""
                    if item.max_confirmed_sell_quantity is None
                    else item.max_confirmed_sell_quantity
                ),
                "unresolved_quantity": (
                    "" if item.unresolved_quantity is None else item.unresolved_quantity
                ),
                "reference_price": (
                    "" if item.reference_price is None else format(item.reference_price, "f")
                ),
                "reference_price_date": (
                    ""
                    if item.reference_price_date is None
                    else item.reference_price_date.isoformat()
                ),
                "reason_codes": ";".join(item.reason_codes),
                "limitations": ";".join(item.limitations),
            }
        )
    return stream.getvalue().encode("utf-8")


def write_json(path: Path, value: object) -> bytes:
    payload = canonical_json_bytes(value)
    path.write_bytes(payload)
    return payload
