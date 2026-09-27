from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_FLOOR, Decimal


def lot_quantity(amount: Decimal, price: Decimal, lot_size: int) -> int:
    if amount <= 0 or price <= 0 or lot_size <= 0:
        return 0
    lots = (amount / price / lot_size).to_integral_value(rounding=ROUND_FLOOR)
    return int(lots) * lot_size


def affordable_quantity(
    desired: int,
    lot_size: int,
    price: Decimal,
    cash: Decimal,
    fee_for_quantity: Callable[[int], Decimal],
) -> int:
    if desired <= 0 or lot_size <= 0 or price <= 0 or cash <= 0:
        return 0
    high, low = desired // lot_size, 0
    while low < high:
        middle = (low + high + 1) // 2
        quantity = middle * lot_size
        if Decimal(quantity) * price + fee_for_quantity(quantity) <= cash:
            low = middle
        else:
            high = middle - 1
    return low * lot_size


def relative_gap(
    target_amount: Decimal,
    current_quantity: int,
    price: Decimal,
    portfolio_value: Decimal,
) -> Decimal:
    if portfolio_value <= 0:
        return Decimal("0")
    return (target_amount - Decimal(current_quantity) * price) / portfolio_value
