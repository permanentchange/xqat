from __future__ import annotations

from datetime import date
from decimal import Decimal

from xqatexp.research.preparation import (
    annualized_roe,
    choose_announce_date,
    derive_quarter_profit,
    derive_ttm_profit,
    next_open_date,
    research_price,
)
from xqatexp.research.tables import ResearchBuilder


def test_raw_unit_and_adjusted_price_boundaries() -> None:
    assert research_price(Decimal("10.25"), Decimal("1.20"), Decimal("1.00")) == Decimal(
        "12.3000000000"
    )
    assert research_price(None, Decimal("1.20"), Decimal("1.00")) is None


def test_announce_date_and_visibility_use_actual_announcement_then_next_open() -> None:
    announced = choose_announce_date("20260430", "20260428")
    assert announced == date(2026, 4, 30)
    assert next_open_date(announced, (date(2026, 4, 30), date(2026, 5, 6))) == date(2026, 5, 6)


def test_quarter_and_ttm_profit_use_only_complete_ytd_components() -> None:
    assert derive_quarter_profit(1, Decimal("10"), None) == Decimal("10")
    assert derive_quarter_profit(2, Decimal("25"), Decimal("10")) == Decimal("15")
    assert derive_quarter_profit(4, Decimal("80"), Decimal("55")) == Decimal("25")
    assert derive_quarter_profit(3, Decimal("55"), None) is None
    assert derive_ttm_profit(4, Decimal("80"), None, None) == Decimal("80")
    assert derive_ttm_profit(3, Decimal("55"), Decimal("70"), Decimal("40")) == Decimal("85")
    assert derive_ttm_profit(3, Decimal("55"), None, Decimal("40")) is None


def test_roe_yearly_is_converted_from_percent_to_ratio() -> None:
    assert annualized_roe(Decimal("12.5")) == Decimal("0.125000000000")
    assert annualized_roe(None) is None


def test_financial_builder_derives_visible_quarter_ttm_losses_and_revisions() -> None:
    def income(period: str, announced: str, profit: str, update: str = "0"):
        return {
            "ts_code": "600000.SH",
            "ann_date": announced,
            "f_ann_date": announced,
            "end_date": period,
            "report_type": "1",
            "comp_type": "1",
            "n_income_attr_p": profit,
            "update_flag": update,
        }

    raw = {
        "income": [
            income("20240930", "20241030", "40"),
            income("20241231", "20250330", "70"),
            income("20250331", "20250430", "-2"),
            income("20250630", "20250830", "-5"),
            income("20250930", "20251030", "55"),
            income("20250930", "20251115", "56", "1"),
        ]
    }
    opens = (
        date(2024, 10, 31),
        date(2025, 3, 31),
        date(2025, 5, 6),
        date(2025, 9, 1),
        date(2025, 10, 31),
        date(2025, 11, 17),
    )
    rows = ResearchBuilder()._financial_rows(raw, opens)
    q2 = next(row for row in rows if row["report_period"] == date(2025, 6, 30))
    assert q2["net_profit_parent_quarter"] == Decimal("-30000.0000")
    assert q2["consecutive_loss_quarters"] == 2
    revisions = [row for row in rows if row["report_period"] == date(2025, 9, 30)]
    assert [row["revision_seq"] for row in revisions] == [1, 2]
    assert revisions[0]["net_profit_parent_ttm"] == Decimal("850000.0000")
    assert revisions[1]["net_profit_parent_ttm"] == Decimal("860000.0000")
