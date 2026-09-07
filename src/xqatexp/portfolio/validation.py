from datetime import date
from decimal import Decimal

from xqatexp.domain.contracts import TargetPortfolio
from xqatexp.domain.enums import AssetType


class PortfolioValidationError(ValueError):
    """A target portfolio violates a cross-module portfolio invariant."""


def validate_target(
    target: TargetPortfolio,
    *,
    etf_id: str,
    maximum_stock_weight: Decimal,
    next_trade_day: date,
) -> TargetPortfolio:
    positions = target.positions
    ids = [item.security_id for item in positions]
    invalid = (
        len(ids) != len(set(ids))
        or target.effective_from != next_trade_day
        or not target.cash_weight.is_finite()
        or target.cash_weight < 0
    )
    total = target.cash_weight
    for item in positions:
        invalid = invalid or not item.target_weight.is_finite() or item.target_weight < 0
        invalid = invalid or item.asset_type not in {AssetType.A_SHARE, AssetType.CSI300_ETF}
        invalid = invalid or (
            item.asset_type is AssetType.A_SHARE and item.target_weight > maximum_stock_weight
        )
        invalid = invalid or (
            item.asset_type is AssetType.CSI300_ETF and item.security_id != etf_id
        )
        total += item.target_weight
    invalid = invalid or abs(total - Decimal("1")) > Decimal("1e-10")
    if invalid:
        raise PortfolioValidationError("PORTFOLIO_INVALID_TARGET: target invariants failed")
    return target
