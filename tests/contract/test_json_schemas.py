from __future__ import annotations

import importlib

import pytest


def _registry():
    try:
        return importlib.import_module("xqatexp.artifacts.schemas").SchemaRegistry()
    except ModuleNotFoundError:
        pytest.fail("xqatexp.artifacts.schemas is not implemented", pytrace=False)


def test_manifest_schema_rejects_unknown_major() -> None:
    """Catches silent acceptance of incompatible Artifact manifests."""
    value = {
        "schema_version": "2.0",
        "artifact_type": "RAW_DATA",
        "artifact_id": "01991a6a-4c00-7000-8000-000000000001",
        "created_at": "2026-09-05T00:00:00Z",
        "producer": {"name": "xqatexp", "version": "0.1.0"},
        "run": None,
        "inputs": [],
        "date_scope": {"start": None, "end": None},
        "files": [],
        "issues": [],
        "limitations": [],
    }
    with pytest.raises(Exception, match="ARTIFACT_SCHEMA_INCOMPATIBLE"):
        _registry().validate_json("artifact_manifest", value)


def test_account_snapshot_schema_rejects_unknown_field() -> None:
    """Catches ignored typos in strict user account inputs."""
    value = {
        "schema_version": "1.0",
        "as_of": "2026-09-05T08:00:00+08:00",
        "currency": "CNY",
        "account_scope": "STRATEGY_MANAGED",
        "scope_completeness": "COMPLETE",
        "positions_completeness": "COMPLETE",
        "available_cash": 1000,
        "managed_total_assets": 1000,
        "excluded_asset_value": None,
        "source_note": "manual export",
        "positions": [],
        "typo_total_asset": 1000,
    }
    with pytest.raises(Exception, match="CONFIG_SCHEMA_INVALID"):
        _registry().validate_json("account_snapshot", value)


def test_registry_exposes_every_versioned_physical_schema() -> None:
    """Catches a result producer with no registered consumer contract."""
    assert _registry().schema_ids == (
        "account_snapshot",
        "artifact_manifest",
        "custom_factor_input",
        "failure_diagnostic",
        "issues",
        "metrics",
        "period_metrics",
        "portfolio_daily",
        "raw_request",
        "raw_response",
        "research_tables",
        "resolved_config",
        "target_history",
        "target_portfolio",
        "target_positions",
        "trade_advice",
        "trades",
        "unfilled",
    )


def test_registry_loads_every_json_contract_from_disk() -> None:
    """Catches a documented JSON result with no machine-readable schema file."""
    registry = _registry()
    assert registry.json_schema_ids == (
        "account_snapshot",
        "artifact_manifest",
        "failure_diagnostic",
        "issues",
        "metrics",
        "raw_request",
        "resolved_config",
        "target_portfolio",
        "trade_advice",
    )
    for schema_id in registry.json_schema_ids:
        assert registry.load_json_schema(schema_id)["type"] == "object"


def test_resolved_config_1_0_accepts_legacy_value_without_analysis_periods() -> None:
    """A minor feature must not invalidate already published schema 1.0 values."""
    value = {
        "schema_version": "1.0",
        "mode": "BACKTEST",
        "strategy_id": "weekly_market_guard_rank_v1",
        "strategy_version": "1.0.0",
        "parameters": {},
        "research_input": {},
        "custom_factor_inputs": [],
        "decision_date": None,
        "start_date": "2025-01-02",
        "end_date": "2025-12-31",
        "account_input": None,
        "previous_target_input": None,
        "output_alias": "result",
        "execution_assumptions": {},
    }

    _registry().validate_json("resolved_config", value)


def test_target_schema_requires_rolling_drawdown_metadata() -> None:
    """Catches an old target schema that cannot identify the drawdown window."""
    value = {
        "schema_version": "1.0",
        "strategy_id": "weekly_market_guard_rank_v1",
        "strategy_version": "1.0.0",
        "decision_date": "2026-09-04",
        "effective_from": "2026-09-07",
        "market_regime": "WEAK",
        "theoretical_drawdown": -0.13,
        "drawdown_overlay_level": "DEFENSIVE",
        "positions": [],
        "transition_records": [],
        "cash_weight": 1,
        "explanations": [],
    }
    with pytest.raises(Exception, match="ARTIFACT_SCHEMA_INCOMPATIBLE"):
        _registry().validate_json("target_portfolio", value)


def _valid_target() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "strategy_id": "weekly_market_guard_rank_v1",
        "strategy_version": "1.0.0",
        "decision_date": "2026-09-04",
        "effective_from": "2026-09-07",
        "market_regime": "STRONG",
        "theoretical_drawdown": -0.01,
        "drawdown_window_trade_days": 60,
        "drawdown_observations": 60,
        "drawdown_overlay_level": "NONE",
        "positions": [
            {
                "security_id": "600000.SH",
                "asset_type": "A_SHARE",
                "target_weight": 0.04,
                "rank": 1,
                "score": 0.8,
                "transition": None,
                "explanation_codes": ["ENTRY_RANK"],
                "holding_age_weeks": 0,
            }
        ],
        "transition_records": [],
        "cash_weight": 0.96,
        "explanations": [],
    }


def test_target_schema_rejects_unknown_nested_position_field() -> None:
    """Catches producer/consumer drift hidden inside an otherwise valid target."""
    target = _valid_target()
    position = target["positions"][0]
    position["unregistered_value"] = 1

    with pytest.raises(Exception, match="ARTIFACT_SCHEMA_INCOMPATIBLE"):
        _registry().validate_json("target_portfolio", target)


def test_account_schema_enforces_unknown_and_scope_completeness() -> None:
    """Catches UNKNOWN holdings treated as listed facts or partial scope as a full NAV."""
    base = {
        "schema_version": "1.0",
        "as_of": "2026-09-05T08:00:00+08:00",
        "currency": "CNY",
        "account_scope": "STRATEGY_MANAGED",
        "scope_completeness": "COMPLETE",
        "positions_completeness": "UNKNOWN",
        "available_cash": 1000,
        "managed_total_assets": 1000,
        "excluded_asset_value": None,
        "source_note": "manual export",
        "positions": [
            {
                "security_id": "600000.SH",
                "quantity": 100,
                "sellable_quantity": 100,
                "market_value": 1000,
                "reference_price": 10,
                "reference_price_date": "2026-09-04",
            }
        ],
    }
    with pytest.raises(Exception, match="CONFIG_SCHEMA_INVALID"):
        _registry().validate_json("account_snapshot", base)

    base["positions"] = []
    base["scope_completeness"] = "PARTIAL"
    with pytest.raises(Exception, match="CONFIG_SCHEMA_INVALID"):
        _registry().validate_json("account_snapshot", base)
