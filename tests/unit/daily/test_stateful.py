from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from xqatexp.daily.stateful import StatefulDailyDecisionService
from xqatexp.domain.contracts import AccountPosition, AccountSnapshot
from xqatexp.domain.enums import (
    AccountScope,
    AccountScopeCompleteness,
    OrderSide,
    PositionCompleteness,
)
from xqatexp.strategy.intents import FullPosition, TradeIntent, TradeIntentDecision
from xqatexp.strategy.state import StrategyPositionState, StrategyStateSnapshot


class _Research:
    decision_date = date(2026, 9, 7)


class _Strategy:
    def generate_stateful_decision(self, research, state, custom, parameters):
        del state, custom, parameters
        return TradeIntentDecision(
            "decision-1",
            "stateful",
            "1.0.0",
            research.decision_date,
            date(2026, 9, 8),
            (
                TradeIntent(
                    "intent-1",
                    "600000.SH",
                    OrderSide.SELL,
                    FullPosition(),
                    ("TEST",),
                ),
            ),
        )


def _state() -> StrategyStateSnapshot:
    return StrategyStateSnapshot(
        "1.0",
        "stateful",
        "1.0.0",
        Decimal("100000"),
        date(2026, 9, 6),
        (
            StrategyPositionState(
                "600000.SH",
                quantity=400,
                remaining_cost_basis=Decimal("4000"),
            ),
        ),
    )


def _account(quantity: int) -> AccountSnapshot:
    return AccountSnapshot(
        "1.0",
        datetime(2026, 9, 7, 8, tzinfo=UTC),
        "CNY",
        AccountScope.STRATEGY_MANAGED,
        AccountScopeCompleteness.COMPLETE,
        PositionCompleteness.COMPLETE,
        Decimal("96000"),
        Decimal("100000"),
        Decimal("0"),
        "test",
        (
            AccountPosition(
                "600000.SH",
                quantity,
                quantity,
                Decimal(quantity * 10),
                Decimal("10"),
                date(2026, 9, 7),
            ),
        ),
    )


def test_stateful_daily_uses_explicit_state_and_accepts_consistent_account() -> None:
    decision = StatefulDailyDecisionService().run(
        _Strategy(),
        _Research(),
        _state(),
        None,
        {},
        account=_account(400),
    )
    assert decision.decision_id == "decision-1"
    assert decision.intents[0].security_id == "600000.SH"


def test_stateful_daily_rejects_account_quantity_conflict() -> None:
    with pytest.raises(ValueError, match="STRATEGY_STATE_ACCOUNT_CONFLICT"):
        StatefulDailyDecisionService().run(
            _Strategy(),
            _Research(),
            _state(),
            None,
            {},
            account=_account(300),
        )
