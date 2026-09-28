from decimal import Decimal

from xqatexp.cli import main
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.state_io import load_strategy_state


def test_state_cli_initializes_and_applies_confirmed_fill(tmp_path) -> None:
    initial = tmp_path / "initial-state.json"
    assert (
        main(
            [
                "state",
                "init",
                "--strategy-id",
                "staged_drawdown_v1",
                "--strategy-version",
                "1.0.0",
                "--initial-capital",
                "100000",
                "--output",
                str(initial),
            ]
        )
        == 0
    )

    updated = tmp_path / "updated-state.json"
    assert (
        main(
            [
                "state",
                "apply-fill",
                "--input",
                str(initial),
                "--execution-date",
                "2026-09-07",
                "--security-id",
                "600000.SH",
                "--side",
                "BUY",
                "--quantity",
                "1000",
                "--execution-price",
                "10",
                "--commission",
                "5",
                "--output",
                str(updated),
            ]
        )
        == 0
    )

    state = load_strategy_state(updated)
    position = state.positions[0]
    assert state.initial_capital == Decimal("100000")
    assert position.quantity == 1000
    assert position.last_trade_side is OrderSide.BUY
    assert position.remaining_cost_basis == Decimal("10005")
