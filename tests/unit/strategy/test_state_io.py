from dataclasses import replace
from datetime import date
from decimal import Decimal

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.state import StrategyPositionState, StrategyStateSnapshot
from xqatexp.strategy.state_io import load_strategy_state, strategy_state_value


def test_strategy_state_round_trip_preserves_execution_facts(tmp_path) -> None:
    state = StrategyStateSnapshot(
        "1.0",
        "stateful",
        "1.0.0",
        Decimal("100000"),
        date(2026, 9, 7),
        (
            StrategyPositionState(
                "600000.SH",
                quantity=400,
                remaining_cost_basis=Decimal("4005"),
                last_trade_side=OrderSide.BUY,
                last_trade_date=date(2026, 9, 7),
                last_trade_quantity=400,
                last_trade_price=Decimal("10"),
                last_buy_price=Decimal("10"),
                cumulative_buy_notional=Decimal("4000"),
            ),
        ),
    )
    path = tmp_path / "state.json"
    path.write_bytes(canonical_json_bytes(strategy_state_value(state)))

    assert load_strategy_state(path) == state


def test_exit_statistics_round_trip_and_legacy_states_remain_readable(tmp_path) -> None:
    from tests.unit.strategy.strategies.staged_drawdown_v1.test_tiered_take_profit import _state
    from tests.unit.strategy.test_state import _fill
    from xqatexp.strategy.state import StrategyStateReducer

    reducer = StrategyStateReducer("staged_drawdown_v1", "1.0.0")
    state = reducer.apply(_state(), _fill(OrderSide.SELL, 300, 100, "11"))
    path = tmp_path / "state.json"
    path.write_bytes(canonical_json_bytes(strategy_state_value(state)))
    assert load_strategy_state(path) == state
    value = strategy_state_value(state)
    for item in value["positions"]:
        del item["exit_base_quantity"]
        del item["exit_sold_quantity"]
    path.write_bytes(canonical_json_bytes(value))
    legacy = load_strategy_state(path)
    assert legacy.positions[0] == replace(
        state.positions[0],
        exit_base_quantity=None,
        exit_sold_quantity=Decimal("0"),
    )
