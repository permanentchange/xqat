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


def test_tiered_daily_only_advances_after_confirmed_fill_and_publishes_fixed_quantity(
    tmp_path,
) -> None:
    from dataclasses import replace

    from tests.contract.test_result_artifacts import _context
    from tests.unit.strategy.strategies.staged_drawdown_v1.test_staged_drawdown import (
        _Research as PriceResearch,
    )
    from tests.unit.strategy.strategies.staged_drawdown_v1.test_tiered_take_profit import (
        _state as holding_state,
    )
    from tests.unit.strategy.strategies.staged_drawdown_v1.test_tiered_take_profit import (
        _strategy as tiered_strategy,
    )
    from xqatexp.domain.enums import OverwritePolicy, RunMode
    from xqatexp.reporting.publisher import ResultArtifactPublisher
    from xqatexp.strategy.intents import FixedQuantity
    from xqatexp.strategy.state_tools import apply_confirmed_fill

    service = StatefulDailyDecisionService()
    strategy = tiered_strategy()
    state = holding_state()
    research = PriceResearch([Decimal("12.5")] * 20, start=date(2026, 8, 19))
    decision = service.run(strategy, research, state, None, {}, account=None)
    assert decision.intents[0].sizing == FixedQuantity(600)
    assert service.run(strategy, research, state, None, {}, account=None) == decision
    assert state.positions[0].exit_base_quantity is None

    context = replace(
        _context(tmp_path),
        mode=RunMode.DAILY_DECISION,
        strategy_id=state.strategy_id,
        parameters=strategy.parameters.as_mapping(),
        decision_date=research.decision_date,
    )
    ResultArtifactPublisher().publish_daily_decision(
        context, decision, state, (), (), OverwritePolicy.ERROR
    )
    import json

    value = json.loads((context.output_path / "trade_intents.json").read_text())
    assert value["intents"][0]["sizing"] == {"kind": "FIXED_QUANTITY", "value": 600}
    state = apply_confirmed_fill(
        state,
        execution_date=date(2026, 9, 8),
        security_id="600000.SH",
        side=OrderSide.SELL,
        quantity=100,
        execution_price=Decimal("12.5"),
    )
    research = PriceResearch([Decimal("12.5")] * 20, start=date(2026, 8, 20))
    assert service.run(strategy, research, state, None, {}, account=None).intents[0].sizing == (
        FixedQuantity(500)
    )
