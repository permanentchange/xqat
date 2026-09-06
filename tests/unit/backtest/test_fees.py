from datetime import date
from decimal import Decimal

from xqatexp.backtest.fees import FeeModel
from xqatexp.domain.enums import AssetType, OrderSide


def test_default_fee_golden_vectors() -> None:
    model = FeeModel.default()
    buy = model.calculate(AssetType.A_SHARE, OrderSide.BUY, Decimal("1000"), date(2026, 9, 4))
    sell = model.calculate(AssetType.A_SHARE, OrderSide.SELL, Decimal("1000"), date(2026, 9, 4))
    assert (buy.commission, buy.transfer_fee, buy.stamp_duty, buy.total) == (
        Decimal("5.00"),
        Decimal("0.01"),
        Decimal("0.00"),
        Decimal("5.01"),
    )
    assert sell.total == Decimal("5.51")


def test_zero_gross_has_no_minimum_commission_and_etf_no_tax() -> None:
    model = FeeModel.default()
    assert (
        model.calculate(AssetType.A_SHARE, OrderSide.BUY, Decimal("0"), date(2026, 1, 1)).total == 0
    )
    assert (
        model.calculate(
            AssetType.CSI300_ETF, OrderSide.SELL, Decimal("1000"), date(2026, 1, 1)
        ).stamp_duty
        == 0
    )


def test_historical_equity_fee_boundaries_are_date_effective() -> None:
    model = FeeModel.default()
    assert model.calculate(
        AssetType.A_SHARE, OrderSide.SELL, Decimal("1000"), date(2023, 8, 27)
    ).total == Decimal("6.01")
    assert model.calculate(
        AssetType.A_SHARE, OrderSide.SELL, Decimal("1000"), date(2022, 4, 28)
    ).total == Decimal("6.02")
