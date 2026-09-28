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



def test_state_cli_applies_confirmed_stock_adjustment(tmp_path) -> None:
    initial = tmp_path / "initial.json"
    assert main(
        [
            "state",
            "init",
            "--strategy-id",
            "staged_drawdown_v1",
            "--initial-capital",
            "100000",
            "--output",
            str(initial),
        ]
    ) == 0
    bought = tmp_path / "bought.json"
    assert main(
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
            "--output",
            str(bought),
        ]
    ) == 0

    adjusted = tmp_path / "adjusted.json"
    assert main(
        [
            "state",
            "apply-stock-adjustment",
            "--input",
            str(bought),
            "--effective-date",
            "2026-09-08",
            "--event-id",
            "distribution-1",
            "--security-id",
            "600000.SH",
            "--kind",
            "STOCK_DISTRIBUTION",
            "--added-quantity",
            "100",
            "--output",
            str(adjusted),
        ]
    ) == 0

    state = load_strategy_state(adjusted)
    assert state.positions[0].quantity == 1100
    assert state.positions[0].remaining_cost_basis == Decimal("10000")
