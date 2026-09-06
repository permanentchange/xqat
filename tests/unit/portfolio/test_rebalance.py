from decimal import Decimal

from tests.unit.portfolio.test_validation import target
from xqatexp.domain.enums import OrderSide
from xqatexp.portfolio.rebalance import LotRule, RebalancePlanner


def _fees(side, quantity, price):
    return Decimal("5.00") if quantity else Decimal("0")


def test_target_quantity_golden_vector_and_buy_difference() -> None:
    plan = RebalancePlanner(_fees).plan(
        target(),
        current_positions={"600000.SH": 100},
        sellable_quantities={"600000.SH": 100},
        portfolio_value=Decimal("100000"),
        reference_prices={"600000.SH": Decimal("10.03")},
        lot_rules={"600000.SH": LotRule(100, 100)},
        cash_budget=Decimal("10000"),
    )
    item = plan.instructions[0]
    assert item.side is OrderSide.BUY
    assert item.theoretical_target_quantity == 300
    assert item.requested_quantity == 200
    assert plan.cash_remaining == Decimal("7989.00")


def test_full_exit_can_sell_odd_lot() -> None:
    from dataclasses import replace

    empty = replace(target(), positions=(), cash_weight=Decimal("1"))
    plan = RebalancePlanner(_fees).plan(
        empty,
        current_positions={"600000.SH": 50},
        sellable_quantities={"600000.SH": 50},
        portfolio_value=Decimal("100000"),
        reference_prices={"600000.SH": Decimal("10")},
        lot_rules={"600000.SH": LotRule(100, 100)},
        cash_budget=Decimal("0"),
    )
    assert plan.instructions[0].side is OrderSide.SELL
    assert plan.instructions[0].requested_quantity == 50
