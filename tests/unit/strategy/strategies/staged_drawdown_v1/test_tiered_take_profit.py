from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from tests.unit.strategy.strategies.staged_drawdown_v1.test_staged_drawdown import _Research
from tests.unit.strategy.test_state import _fill
from xqatexp.backtest.account_events import SplitApplied, StockDistributionApplied
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.intents import (
    FixedNotional,
    FixedQuantity,
    FullPosition,
    InitialCapitalFraction,
)
from xqatexp.strategy.state import (
    StrategyPositionState,
    StrategyStateReducer,
    StrategyStateSnapshot,
    StrategyStateView,
)
from xqatexp.strategy.strategies.staged_drawdown_v1.parameters import (
    normalize_staged_drawdown_parameters,
)
from xqatexp.strategy.strategies.staged_drawdown_v1.strategy import StagedDrawdownStrategy


def _strategy() -> StagedDrawdownStrategy:
    return StagedDrawdownStrategy({"security_id": "600000.SH", "take_profit_mode": "tiered"})


def _state(quantity: int = 1000) -> StrategyStateSnapshot:
    return StrategyStateReducer("staged_drawdown_v1", "1.0.0").replay(
        Decimal("100000"), (_fill(OrderSide.BUY, quantity, quantity, "10"),)
    )


def _decision(close: str, state: StrategyStateSnapshot, lot: int = 100):
    return _strategy().generate_stateful_decision(
        _Research([Decimal("10")] * 19 + [Decimal(close)], sell_lot=lot),
        StrategyStateView(state),
        None,
        {},
    )


@pytest.mark.parametrize(
    ("close", "quantity"),
    [
        ("10.999", None),
        ("11", 300),
        ("11.999", 300),
        ("12", 600),
        ("12.5", 600),
        ("12.999", 600),
        ("13", 1000),
        ("13.5", 1000),
    ],
)
def test_tiers_use_highest_crossed_threshold(close, quantity) -> None:
    state = _state()
    decision = _decision(close, state)
    if quantity is None:
        assert decision.intents == ()
    elif quantity == 1000:
        assert isinstance(decision.intents[0].sizing, FullPosition)
    else:
        assert decision.intents[0].sizing == FixedQuantity(quantity)
    # Suggestions do not change confirmed state or lock the quantity base.
    assert state.positions[0].exit_base_quantity is None
    assert _decision(close, state) == decision


def test_partial_fills_only_sell_remaining_gap_and_completed_tiers_do_not_repeat() -> None:
    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = _state()
    state = reducer.apply(state, _fill(OrderSide.SELL, 600, 400, "12.5"))
    assert state.positions[0].exit_base_quantity == 1000
    assert state.positions[0].exit_sold_quantity == 400
    assert _decision("12.5", state).intents[0].sizing == FixedQuantity(200)
    assert _decision("11.5", state).intents == ()
    state = reducer.apply(state, _fill(OrderSide.SELL, 200, 0, "12.5"))
    assert state.positions[0].exit_sold_quantity == 400
    state = reducer.apply(state, _fill(OrderSide.SELL, 200, 200, "12.5"))
    assert _decision("12.5", state).intents == ()
    assert _decision("11.5", state).intents == ()
    assert isinstance(_decision("13", state).intents[0].sizing, FullPosition)
    state = reducer.apply(state, _fill(OrderSide.SELL, 400, 200, "13"))
    assert isinstance(_decision("13", state).intents[0].sizing, FullPosition)
    state = reducer.apply(state, _fill(OrderSide.SELL, 200, 200, "13"))
    assert state.positions[0].quantity == 0
    assert state.positions[0].exit_base_quantity is None
    assert state.positions[0].exit_sold_quantity == 0
    closes = [Decimal("10") - Decimal("0.06") * i for i in range(20)]
    decision = _strategy().generate_stateful_decision(
        _Research(closes),
        StrategyStateView(state),
        None,
        {},
    )
    assert decision.intents[0].reason_codes == ("INITIAL_ENTRY",)


def test_small_positions_exit_and_rounding_does_not_cause_a_second_sale() -> None:
    assert isinstance(_decision("11", _state(300)).intents[0].sizing, FullPosition)
    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = _state(450)
    assert _decision("11", state).intents[0].sizing == FixedQuantity(100)
    state = reducer.apply(state, _fill(OrderSide.SELL, 100, 100, "11"))
    assert _decision("11", state).intents == ()
    assert _decision("12", state).intents[0].sizing == FixedQuantity(100)
    state = reducer.apply(state, _fill(OrderSide.SELL, 100, 100, "12"))
    assert state.positions[0].quantity == 250
    assert isinstance(_decision("13", state).intents[0].sizing, FullPosition)
    assert _decision("11", _state(450), lot=1).intents[0].sizing == FixedQuantity(135)
    assert _decision("11", _state(), lot=200).intents[0].sizing == FixedQuantity(200)


def test_partial_gap_below_lot_exits_remaining_position() -> None:
    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = reducer.apply(_state(), _fill(OrderSide.SELL, 300, 250, "11"))
    assert isinstance(_decision("11", state).intents[0].sizing, FullPosition)


def test_average_cost_includes_buy_fees() -> None:
    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = reducer.replay(Decimal("100000"), (_fill(OrderSide.BUY, 1000, 1000, "10", fees="10"),))
    assert _decision("11", state).intents == ()
    assert _decision("11.011", state).intents[0].sizing == FixedQuantity(300)


@pytest.mark.parametrize("value", [0, 1, "true", "false", None])
def test_add_after_sell_requires_boolean_parameter(value):
    with pytest.raises(ValueError, match="allow_add_after_sell"):
        StagedDrawdownStrategy({"security_id": "600000.SH", "allow_add_after_sell": value})


def test_add_after_sell_uses_last_buy_anchor_and_does_not_return_sold_budget():
    from xqatexp.daily.stateful import StatefulDailyDecisionService

    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = reducer.apply(_state(), _fill(OrderSide.SELL, 300, 300, "11"))
    enabled = StagedDrawdownStrategy(
        {
            "security_id": "600000.SH",
            "take_profit_mode": "tiered",
            "allow_add_after_sell": True,
            "max_capital_fraction": Decimal("0.30"),
        }
    )
    service = StatefulDailyDecisionService()
    research = _Research([Decimal("9.1")] * 20, start=date(2026, 8, 19))
    assert service.run(enabled, research, state, None, {}, account=None).intents == ()
    research = _Research([Decimal("9")] * 20, start=date(2026, 8, 19))
    disabled = service.run(_strategy(), research, state, None, {}, account=None)
    assert disabled.intents == ()
    assert disabled.diagnostics.values["add_blocked_after_sell"] is True
    decision = service.run(enabled, research, state, None, {}, account=None)
    assert decision.intents[0].sizing == InitialCapitalFraction(Decimal("0.10"))
    assert decision.diagnostics.values["signal"] == "ADD_ON_DECLINE"
    assert decision.diagnostics.values["add_budget_remaining"] == Decimal("20000")
    assert service.run(enabled, research, state, None, {}, account=None) == decision
    assert state.positions[0].exit_sold_quantity == 300
    # A partial confirmed BUY resets exit progress; the unfilled suggestion did not.
    state = reducer.apply(state, _fill(OrderSide.BUY, 1100, 100, "9"))
    position = state.positions[0]
    assert position.quantity == 800
    assert position.exit_base_quantity is None
    assert position.exit_sold_quantity == 0
    assert position.cumulative_buy_notional == Decimal("10900")
    decision = service.run(
        enabled,
        _Research([Decimal("12")] * 20, start=date(2026, 8, 19)),
        state,
        None,
        {},
        account=None,
    )
    assert decision.intents[0].sizing == FixedQuantity(400)


def test_readdition_respects_exhausted_and_partial_remaining_budget():
    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = reducer.apply(_state(), _fill(OrderSide.SELL, 300, 300, "11"))
    research = _Research([Decimal("9")] * 20)
    for cap, sizing in [("0.10", None), ("0.15", FixedNotional(Decimal("5000")))]:
        strategy = StagedDrawdownStrategy(
            {
                "security_id": "600000.SH",
                "take_profit_mode": "tiered",
                "allow_add_after_sell": True,
                "max_capital_fraction": Decimal(cap),
            }
        )
        decision = strategy.generate_stateful_decision(research, StrategyStateView(state), None, {})
        if sizing is None:
            assert decision.intents == ()
            assert decision.diagnostics.values["add_budget_exhausted"] is True
        else:
            assert decision.intents[0].sizing == sizing


def test_adjustments_preserve_exit_proportions_and_new_buy_resets_statistics() -> None:
    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = reducer.apply(_state(), _fill(OrderSide.SELL, 300, 300, "11"))
    state = reducer.apply(state, StockDistributionApplied("distribution", "600000.SH", 70))
    assert state.positions[0].exit_base_quantity == 1100
    assert state.positions[0].exit_sold_quantity == 330
    assert _decision("10", state).intents == ()  # 10% above adjusted average cost.
    state = reducer.apply(state, SplitApplied("split", "600000.SH", 770, 0))
    assert state.positions[0].exit_base_quantity == 2200
    assert state.positions[0].exit_sold_quantity == 660
    assert _decision("5.5", state).intents[0].sizing == FixedQuantity(600)
    state = reducer.apply(state, _fill(OrderSide.BUY, 100, 100, "5"))
    assert state.positions[0].exit_base_quantity is None
    assert state.positions[0].exit_sold_quantity == 0
    assert _decision("5.5", state).intents[0].sizing == FixedQuantity(900)


def test_unknown_legacy_sell_history_is_rejected_only_in_tiered_mode() -> None:
    state = _state()
    state = replace(state, positions=(replace(state.positions[0], last_trade_side=OrderSide.SELL),))
    with pytest.raises(ValueError, match="replay confirmed fills"):
        _decision("11", state)

    repeat = StagedDrawdownStrategy({"security_id": "600000.SH"})
    assert repeat.generate_stateful_decision(
        _Research([Decimal("11")] * 20),
        StrategyStateView(state),
        None,
        {},
    ).intents[0].reason_codes == ("TAKE_PROFIT",)
    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = reducer.apply(state, _fill(OrderSide.SELL, 100, 100, "11"))
    assert state.positions[0].exit_base_quantity is None
    with pytest.raises(ValueError, match="replay confirmed fills"):
        _decision("11", state)


@pytest.mark.parametrize("sold", [100, 300, 600])
@pytest.mark.parametrize("added", [1, 7, 33])
def test_fractional_adjustments_preserve_quantity_conservation(sold, added) -> None:
    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = reducer.apply(_state(), _fill(OrderSide.SELL, sold, sold, "11"))
    state = reducer.apply(state, StockDistributionApplied("adjust", "600000.SH", added))
    position = state.positions[0]
    assert position.exit_base_quantity - position.exit_sold_quantity == position.quantity


@pytest.mark.parametrize("lot", [0, -1, True, "100"])
def test_invalid_security_rules_are_rejected(lot) -> None:
    with pytest.raises(ValueError, match="sell lot size"):
        _decision("11", _state(), lot=lot)


@pytest.mark.parametrize(
    "parameters",
    [
        {"take_profit_mode": "unknown"},
        {"take_profit_levels": []},
        {"take_profit_levels": "0.1"},
        {"take_profit_levels": [0.1, 0.2]},
        {"take_profit_levels": [0, 0.2, 0.3]},
        {"take_profit_levels": [0.1, 0.1, 0.3]},
        {"take_profit_levels": [0.3, 0.2, 0.1]},
        {"take_profit_levels": [0.1, float("inf"), 0.3]},
        {"take_profit_sell_fractions": [0.3, 0.3, 0.3]},
        {"take_profit_sell_fractions": [0, 0.5, 0.5]},
        {"take_profit_sell_fractions": [0.3, float("nan"), 0.4]},
        {"take_profit_sell_fractions": [True, 0.3, 0.4]},
        {"take_profit_threshold": 0.1},
        {"sell_fraction": 0.2},
    ],
)
def test_invalid_tier_configuration_fails_before_running(parameters) -> None:
    with pytest.raises(ValueError, match="CONFIG_VALUE_INVALID"):
        normalize_staged_drawdown_parameters(
            {"security_id": "600000.SH", "take_profit_mode": "tiered", **parameters},
            {},
        )


def test_normalized_tier_parameters_are_idempotent_and_single_tier_is_full_exit() -> None:
    parameters = normalize_staged_drawdown_parameters(
        {"security_id": "600000.SH", "take_profit_mode": "tiered"},
        {},
    )
    assert normalize_staged_drawdown_parameters(parameters, {}) == parameters
    assert _strategy().parameters.as_mapping() == parameters
    strategy = StagedDrawdownStrategy(
        {
            "security_id": "600000.SH",
            "take_profit_mode": "tiered",
            "take_profit_levels": [0.1],
            "take_profit_sell_fractions": [1],
        }
    )
    assert isinstance(
        strategy.generate_stateful_decision(
            _Research([Decimal("11")] * 20),
            StrategyStateView(_state()),
            None,
            {},
        )
        .intents[0]
        .sizing,
        FullPosition,
    )


def test_inconsistent_exit_state_and_nonpositive_fixed_quantity_are_rejected() -> None:
    with pytest.raises(ValueError, match="exit quantities"):
        StrategyPositionState("600000.SH", quantity=100, exit_base_quantity=Decimal("200"))
    for quantity in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            FixedQuantity(quantity)
