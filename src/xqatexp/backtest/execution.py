from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from xqatexp.backtest.fees import FeeModel
from xqatexp.domain.contracts import ExecutionRecord, RebalanceInstruction
from xqatexp.domain.enums import AssetType, FillStatus, OrderSide, UnfilledReason
from xqatexp.domain.numeric import quantize_price


@dataclass(frozen=True, slots=True)
class ExecutionFacts:
    asset_type: AssetType
    open_raw: Decimal
    high_raw: Decimal
    low_raw: Decimal
    up_limit: Decimal
    down_limit: Decimal
    volume_shares: int
    price_tick: Decimal
    suspended: bool
    limit_up_locked: bool
    limit_down_locked: bool


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    record: ExecutionRecord | None
    unfilled_reason: UnfilledReason | None
    unfilled_quantity: int


class ExecutionSimulator:
    def __init__(self, fees: FeeModel) -> None:
        self._fees = fees

    def execute(
        self,
        instruction: RebalanceInstruction,
        facts: ExecutionFacts,
        execution_date: date,
        cash_available: Decimal,
        holding_quantity: int,
        sellable_quantity: int,
        max_volume_participation: Decimal,
        slippage_bps: Decimal,
    ) -> ExecutionOutcome:
        if facts.suspended:
            return ExecutionOutcome(None, UnfilledReason.SUSPENDED, instruction.requested_quantity)
        if instruction.side is OrderSide.BUY and facts.limit_up_locked:
            return ExecutionOutcome(
                None, UnfilledReason.LIMIT_UP_LOCKED, instruction.requested_quantity
            )
        if instruction.side is OrderSide.SELL and facts.limit_down_locked:
            return ExecutionOutcome(
                None, UnfilledReason.LIMIT_DOWN_LOCKED, instruction.requested_quantity
            )
        price = self._price(instruction.side, facts, slippage_bps)
        if price is None:
            return ExecutionOutcome(
                None, UnfilledReason.NO_EXECUTION_PRICE, instruction.requested_quantity
            )
        raw_capacity = int(
            (Decimal(facts.volume_shares) * max_volume_participation).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        full_odd_lot_exit = (
            instruction.side is OrderSide.SELL
            and instruction.requested_quantity == holding_quantity
        )
        capacity = (
            raw_capacity
            if full_odd_lot_exit
            else raw_capacity // instruction.lot_size * instruction.lot_size
        )
        quantity = min(instruction.requested_quantity, capacity)
        reason = UnfilledReason.VOLUME_CAP if quantity < instruction.requested_quantity else None
        if instruction.side is OrderSide.SELL:
            quantity = min(quantity, holding_quantity, sellable_quantity)
            if quantity == 0:
                return ExecutionOutcome(
                    None, UnfilledReason.SELLABLE_INSUFFICIENT, instruction.requested_quantity
                )
        else:
            quantity = self._cash_quantity(
                quantity,
                instruction.lot_size,
                price,
                cash_available,
                facts.asset_type,
                execution_date,
            )
            if quantity == 0:
                return ExecutionOutcome(
                    None, UnfilledReason.CASH_INSUFFICIENT, instruction.requested_quantity
                )
            if quantity < instruction.requested_quantity and reason is None:
                reason = UnfilledReason.CASH_INSUFFICIENT
        gross = Decimal(quantity) * price
        fees = self._fees.calculate(facts.asset_type, instruction.side, gross, execution_date)
        status = (
            FillStatus.FILLED
            if quantity == instruction.requested_quantity
            else FillStatus.PARTIALLY_FILLED
        )
        return ExecutionOutcome(
            ExecutionRecord(
                execution_date,
                instruction.security_id,
                instruction.side,
                instruction.requested_quantity,
                quantity,
                price,
                gross,
                fees,
                status,
                reason,
            ),
            reason,
            instruction.requested_quantity - quantity,
        )

    @staticmethod
    def _price(side: OrderSide, facts: ExecutionFacts, slippage_bps: Decimal) -> Decimal | None:
        direction = Decimal("1") if side is OrderSide.BUY else Decimal("-1")
        candidate = facts.open_raw * (Decimal("1") + direction * slippage_bps / 10_000)
        price = quantize_price(candidate, facts.price_tick)
        if side is OrderSide.BUY:
            lower = max(facts.open_raw, facts.low_raw, facts.down_limit)
            upper = min(facts.high_raw, facts.up_limit)
            clamped = min(max(price, lower), upper)
            result = (clamped / facts.price_tick).to_integral_value(
                rounding=ROUND_FLOOR
            ) * facts.price_tick
        else:
            lower = max(facts.low_raw, facts.down_limit)
            upper = min(facts.open_raw, facts.high_raw, facts.up_limit)
            clamped = max(min(price, upper), lower)
            result = (clamped / facts.price_tick).to_integral_value(
                rounding=ROUND_CEILING
            ) * facts.price_tick
        return result if lower <= result <= upper else None

    def _cash_quantity(
        self,
        desired: int,
        lot_size: int,
        price: Decimal,
        cash: Decimal,
        asset_type: AssetType,
        on_date: date,
    ) -> int:
        high, low = desired // lot_size, 0
        while low < high:
            middle = (low + high + 1) // 2
            quantity = middle * lot_size
            fees = self._fees.calculate(
                asset_type, OrderSide.BUY, Decimal(quantity) * price, on_date
            )
            if Decimal(quantity) * price + fees.total <= cash:
                low = middle
            else:
                high = middle - 1
        return low * lot_size
