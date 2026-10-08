from dataclasses import replace
from decimal import Decimal

import pytest

from tests.unit.strategy.strategies.staged_drawdown_v1.test_staged_drawdown import (
    _flat_state,
    _position_state,
    _Research,
)
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.state import StrategyPositionState, StrategyStateView
from xqatexp.strategy.strategies.staged_drawdown_v1.strategy import StagedDrawdownStrategy


def _prices():
    return [Decimal("10")] * 10 + [Decimal("10") - Decimal("0.06") * index for index in range(20)]


def _strategy(**overrides):
    return StagedDrawdownStrategy(
        {"security_id": "600000.SH", "entry_confirmation_mode": "ma_rebound", **overrides}
    )


def _decision(prices, *, state=None, strategy=None):
    return (strategy or _strategy()).generate_stateful_decision(
        _Research(prices), state or _flat_state(), None, {}
    )


def test_decline_waits_then_enters_after_rebound_without_current_decline():
    waiting = _decision(_prices())
    assert waiting.intents == ()
    assert waiting.diagnostics.values["entry_waiting"] is True
    confirmed = _decision([*_prices(), Decimal("9.2")])
    assert confirmed.intents[0].side is OrderSide.BUY
    assert confirmed.diagnostics.values["slow_decline"] is False
    assert confirmed.diagnostics.values["entry_setup_age_trade_days"] == 1
    assert confirmed.diagnostics.values["entry_confirmation_ma"] == Decimal("9.00")
    assert confirmed.diagnostics.values["signal"] == "INITIAL_ENTRY"


@pytest.mark.parametrize(
    ("suffix", "above_ma", "price_up"),
    [
        (["8.87"], False, True),
        (["9.2", "9.19"], True, False),
        (["9.2", "9.2"], True, False),
        (["8.95"], False, True),
    ],
)
def test_both_strict_confirmation_comparisons_are_required(suffix, above_ma, price_up):
    decision = _decision(_prices() + [Decimal(value) for value in suffix])
    assert decision.intents == ()
    assert decision.diagnostics.values["entry_confirmation_above_ma"] is above_ma
    assert decision.diagnostics.values["entry_confirmation_price_up"] is price_up


@pytest.mark.parametrize(("age", "enters"), [(10, True), (11, False)])
def test_window_includes_tenth_trading_day_and_excludes_eleventh(age, enters):
    decision = _decision(_prices() + [Decimal("9.2")] * (age - 1) + [Decimal("9.21")])
    assert bool(decision.intents) is enters
    assert decision.diagnostics.values["entry_setup_age_trade_days"] == (age if enters else None)
    assert decision.diagnostics.values["entry_confirmation_passed"] is True


def test_new_decline_refreshes_latest_setup_and_zero_window_requires_same_day():
    strategy = _strategy(entry_confirmation_window_days=0)
    # A sharp recovery above MA5 still leaves the 20-close cumulative decline beyond 10%.
    prices = [Decimal("10") - Decimal("0.08") * index for index in range(19)] + [Decimal("8.85")]
    decision = _decision(prices, strategy=strategy)
    assert decision.intents[0].side is OrderSide.BUY
    assert decision.diagnostics.values["entry_setup_age_trade_days"] == 0
    decision = _decision([*_prices(), Decimal("9.2")], strategy=strategy)
    assert decision.intents == ()
    refreshed = _decision([*_prices(), Decimal("8.80")])
    assert (
        refreshed.diagnostics.values["entry_setup_date"]
        == _Research([*_prices(), Decimal("8.80")]).decision_date
    )
    assert refreshed.diagnostics.values["entry_setup_age_trade_days"] == 0


def test_completed_cycle_cannot_reuse_decline_before_confirmed_exit():
    prices = [*_prices(), Decimal("9.2")]
    research = _Research(prices)
    old_exit = StrategyPositionState(
        "600000.SH", last_trade_side=OrderSide.SELL, last_trade_date=research.decision_date
    )
    state = StrategyStateView(replace(_flat_state().snapshot, positions=(old_exit,)))
    assert _decision(prices, state=state).intents == ()
    # A setup on the exit date is permitted; only older opportunities are discarded.
    old_exit = replace(old_exit, last_trade_date=research._days[-2])
    state = StrategyStateView(replace(state.snapshot, positions=(old_exit,)))
    assert _decision(prices, state=state).intents[0].side is OrderSide.BUY


def test_disabled_mode_keeps_immediate_entry_and_original_warmup():
    prices = _prices()[-20:]
    strategy = _strategy(entry_confirmation_mode="none")
    decision = _decision(prices, strategy=strategy)
    assert decision.intents[0].side is OrderSide.BUY
    assert strategy.declaration.lookback_trade_days == 20


def test_confirmation_does_not_gate_additions_or_take_profit():
    state = _position_state()
    addition = _decision([Decimal("8.8")] * 30, state=state)
    assert addition.diagnostics.values["entry_confirmation_passed"] is False
    assert addition.diagnostics.values["signal"] == "ADD_ON_DECLINE"
    profit = _decision([Decimal("12")] * 30, state=state)
    assert profit.diagnostics.values["signal"] == "TAKE_PROFIT"


def test_confirmation_uses_research_prices_instead_of_raw_prices():
    research = _Research([*_prices(), Decimal("9.2")])
    original_slice = research.slice

    def adjusted_slice(day):
        view = original_slice(day)
        original_history = view.history
        view.history = lambda *args: tuple(
            {**row, "close_raw": Decimal("50")} for row in original_history(*args)
        )
        return view

    research.slice = adjusted_slice
    decision = _strategy().generate_stateful_decision(research, _flat_state(), None, {})
    assert decision.intents[0].side is OrderSide.BUY
    assert decision.diagnostics.values["entry_confirmation_ma"] == Decimal("9.00")


@pytest.mark.parametrize(("ma_days", "window", "required"), [(5, 10, 30), (40, 10, 40), (2, 0, 20)])
def test_declaration_and_readiness_require_complete_history(ma_days, window, required):
    strategy = _strategy(entry_confirmation_ma_days=ma_days, entry_confirmation_window_days=window)
    assert strategy.declaration.lookback_trade_days == required
    assert [r.lookback_trade_days for r in strategy.declaration.data_requirements] == [
        required,
        required,
    ]
    with pytest.raises(ValueError, match="STRATEGY_WARMUP_INSUFFICIENT"):
        _decision([Decimal("10")] * (required - 1), strategy=strategy)


@pytest.mark.parametrize(
    "overrides",
    [
        {"entry_confirmation_mode": "unknown"},
        {"entry_confirmation_mode": True},
        {"entry_confirmation_ma_days": 1},
        {"entry_confirmation_ma_days": True},
        {"entry_confirmation_ma_days": 2.5},
        {"entry_confirmation_window_days": -1},
        {"entry_confirmation_window_days": False},
        {"entry_confirmation_window_days": 1.5},
    ],
)
def test_invalid_confirmation_parameters_fail_before_decision(overrides):
    with pytest.raises(ValueError, match="CONFIG_VALUE_INVALID"):
        _strategy(**overrides)


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "null", "nan"])
def test_confirmation_rejects_incomplete_or_invalid_price_history(corruption):
    research = _Research([*_prices(), Decimal("9.2")])
    original_slice = research.slice

    def broken_slice(day):
        view = original_slice(day)
        original_history = view.history

        def history(*args):
            rows = list(original_history(*args))
            if corruption == "missing":
                rows.pop(0)
            elif corruption == "duplicate":
                rows[0] = rows[1]
            else:
                rows[0] = {**rows[0], "research_close": None if corruption == "null" else "NaN"}
            return tuple(rows)

        view.history = history
        return view

    research.slice = broken_slice
    with pytest.raises(ValueError, match="DATA_REQUIRED_MISSING"):
        _strategy().generate_stateful_decision(research, _flat_state(), None, {})
