from decimal import Decimal

import pytest

from xqatexp.performance.contribution import contribution


def test_contribution_golden_vector_conserves_daily_return() -> None:
    result = contribution(
        nav_begin=Decimal("1000"),
        nav_end=Decimal("978"),
        stock_begin=Decimal("600"),
        stock_end=Decimal("620"),
        stock_buys=Decimal("100"),
        stock_sells=Decimal("50"),
        stock_dividends=Decimal("10"),
        etf_begin=Decimal("200"),
        etf_end=Decimal("204"),
        etf_buys=Decimal("0"),
        etf_sells=Decimal("0"),
        cash_begin=Decimal("200"),
        cash_end=Decimal("154"),
    )
    assert result.stock == Decimal("-0.020")
    assert result.etf == Decimal("0.004")
    assert result.cash_cost == Decimal("-0.006")
    assert float(result.total) == pytest.approx(-0.022)
