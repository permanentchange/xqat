from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from xqatexp.domain.contracts import ExecutionFees
from xqatexp.domain.enums import AssetType, OrderSide
from xqatexp.domain.numeric import quantize_fen


@dataclass(frozen=True, slots=True)
class FeeRates:
    commission_rate: Decimal
    minimum_commission: Decimal
    transfer_fee_rate: Decimal
    sell_stamp_duty_rate: Decimal


class FeeModel:
    def __init__(self, equity_rates: FeeRates, etf_rates: FeeRates) -> None:
        self._equity = equity_rates
        self._etf = etf_rates

    @classmethod
    def default(cls) -> FeeModel:
        return cls(
            FeeRates(Decimal("0.0003"), Decimal("5"), Decimal("0.00001"), Decimal("0.0005")),
            FeeRates(Decimal("0.0003"), Decimal("5"), Decimal("0"), Decimal("0")),
        )

    def calculate(
        self, asset_type: AssetType, side: OrderSide, gross_amount: Decimal, on_date: date
    ) -> ExecutionFees:
        del on_date
        if gross_amount == 0:
            return ExecutionFees(Decimal("0"), Decimal("0"), Decimal("0"))
        if gross_amount < 0:
            raise ValueError("BACKTEST_ACCOUNT_CONSERVATION_BROKEN: negative gross")
        rates = self._equity if asset_type is AssetType.A_SHARE else self._etf
        raw_commission = max(gross_amount * rates.commission_rate, rates.minimum_commission)
        raw_transfer = gross_amount * rates.transfer_fee_rate
        raw_stamp = (
            gross_amount * rates.sell_stamp_duty_rate
            if side is OrderSide.SELL and asset_type is AssetType.A_SHARE
            else Decimal("0")
        )
        total = quantize_fen(raw_commission + raw_transfer + raw_stamp)
        commission = quantize_fen(raw_commission)
        transfer = quantize_fen(raw_transfer)
        stamp = total - commission - transfer
        if raw_stamp == 0:
            commission += stamp
            stamp = Decimal("0.00")
        return ExecutionFees(commission, transfer, stamp)
