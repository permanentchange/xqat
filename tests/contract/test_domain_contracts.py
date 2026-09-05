from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest


def _load(name: str):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        pytest.fail(f"{name} is not implemented", pytrace=False)


def test_core_enum_values_match_shared_contract() -> None:
    """Catches enum drift between producers and consumers."""
    enums = _load("xqatexp.domain.enums")
    assert [value.value for value in enums.RunMode] == [
        "BACKTEST",
        "DAILY_TARGET",
        "DAILY_ADVICE",
    ]
    assert [value.value for value in enums.AssetType] == [
        "A_SHARE",
        "CSI300_ETF",
        "CASH",
        "CSI300_INDEX",
    ]
    assert [value.value for value in enums.PositionCompleteness] == [
        "COMPLETE",
        "PARTIAL",
        "UNKNOWN",
    ]
    assert [value.value for value in enums.AccountScopeCompleteness] == [
        "COMPLETE",
        "PARTIAL",
        "UNKNOWN",
    ]


def test_target_position_is_immutable_and_uses_decimal_weight() -> None:
    """Catches mutable targets or float weights crossing the domain boundary."""
    contracts = _load("xqatexp.domain.contracts")
    enums = _load("xqatexp.domain.enums")
    position = contracts.TargetPosition(
        security_id="600000.SH",
        asset_type=enums.AssetType.A_SHARE,
        target_weight=Decimal("0.04"),
        rank=1,
        score=0.875,
        transition=None,
        explanation_codes=("ENTRY_RANK",),
        holding_age_weeks=0,
    )
    assert position.target_weight == Decimal("0.04")
    with pytest.raises(FrozenInstanceError):
        position.target_weight = Decimal("0.05")


def test_target_portfolio_records_rolling_drawdown_contract() -> None:
    """Catches loss of the approved 60-trading-day drawdown metadata."""
    contracts = _load("xqatexp.domain.contracts")
    enums = _load("xqatexp.domain.enums")
    target = contracts.TargetPortfolio(
        strategy_id="weekly_market_guard_rank_v1",
        strategy_version="1.0.0",
        decision_date=date(2026, 9, 4),
        effective_from=date(2026, 9, 7),
        market_regime=enums.MarketRegime.WEAK,
        theoretical_drawdown=Decimal("-0.13"),
        drawdown_window_trade_days=60,
        drawdown_observations=60,
        drawdown_overlay_level="DEFENSIVE",
        positions=(),
        transition_records=(),
        cash_weight=Decimal("1"),
        explanations=(),
    )
    assert target.drawdown_window_trade_days == 60
    assert target.drawdown_observations == 60


def test_issue_is_immutable_and_keeps_serializable_evidence() -> None:
    """Catches mutable issue envelopes shared across stages."""
    issues = _load("xqatexp.domain.issues")
    enums = _load("xqatexp.domain.enums")
    issue = issues.Issue(
        code="DATA_REQUIRED_MISSING",
        severity=enums.Severity.ERROR,
        stage="readiness",
        scope="market_daily",
        message="Required data is missing.",
        evidence={"rows": 0},
        suggested_action="Build the missing date range.",
    )
    assert issue.evidence == {"rows": 0}
    with pytest.raises(FrozenInstanceError):
        issue.code = "CONFIG_VALUE_INVALID"
