from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from xqatexp.domain.contracts import TargetPortfolio, TargetPosition
from xqatexp.domain.enums import AssetType, MarketRegime
from xqatexp.portfolio.validation import PortfolioValidationError, validate_target


def target(weight=Decimal("0.04"), cash=Decimal("0.96")):
    return TargetPortfolio(
        "weekly_market_guard_rank_v1",
        "1",
        date(2026, 9, 4),
        date(2026, 9, 7),
        MarketRegime.NEUTRAL,
        Decimal("0"),
        60,
        60,
        "NONE",
        (TargetPosition("600000.SH", AssetType.A_SHARE, weight, 1, 90.0, None, (), 1),),
        (),
        cash,
        (),
    )


def test_valid_target_passes_without_normalization() -> None:
    original = target()
    assert (
        validate_target(
            original,
            etf_id="510300.SH",
            maximum_stock_weight=Decimal("0.05"),
            next_trade_day=date(2026, 9, 7),
        )
        is original
    )


def test_invalid_weight_sum_is_rejected_not_repaired() -> None:
    with pytest.raises(PortfolioValidationError, match="PORTFOLIO_INVALID_TARGET"):
        validate_target(
            replace(target(), cash_weight=Decimal("0.95")),
            etf_id="510300.SH",
            maximum_stock_weight=Decimal("0.05"),
            next_trade_day=date(2026, 9, 7),
        )
