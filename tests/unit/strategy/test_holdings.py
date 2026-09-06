from decimal import Decimal

from xqatexp.domain.enums import MarketRegime
from xqatexp.strategy.weekly_strategy import allocate_budget, select_holdings


def test_budget_golden_vectors_and_pool_shortage() -> None:
    assert allocate_budget(MarketRegime.STRONG, 20, Decimal("0.05")) == (
        Decimal("0.0375"),
        Decimal("0.15"),
        Decimal("0.10"),
    )
    assert allocate_budget(MarketRegime.NEUTRAL, 10, Decimal("0.05")) == (
        Decimal("0.04"),
        Decimal("0.30"),
        Decimal("0.30"),
    )
    assert allocate_budget(MarketRegime.WEAK, 0, Decimal("0.05")) == (
        Decimal("0"),
        Decimal("0.10"),
        Decimal("0.90"),
    )
    assert allocate_budget(MarketRegime.STRONG, 10, Decimal("0.05")) == (
        Decimal("0.05"),
        Decimal("0.15"),
        Decimal("0.35"),
    )


def test_holding_age_rank_and_hard_filter_transition_order() -> None:
    ranked = ("NEW", "MIN", "MID", "MAX", "EXTRA")
    selected = select_holdings(
        ranked,
        {"MIN": 1, "MID": 2, "MAX": 8, "FILTERED": 1},
        target_count=3,
        entry_rank=3,
        exit_rank=4,
        min_holding_weeks=2,
        max_holding_weeks=8,
    )
    assert selected == {"NEW": 1, "MIN": 2, "MID": 3}
