from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from xqatexp.domain.contracts import RebalanceInstruction, TargetPortfolio
from xqatexp.domain.enums import OrderSide
from xqatexp.domain.numeric import quantize_fen


@dataclass(frozen=True, slots=True)
class LotRule:
    buy_lot_size: int
    sell_lot_size: int


@dataclass(frozen=True, slots=True)
class RebalancePlan:
    instructions: tuple[RebalanceInstruction, ...]
    cash_remaining: Decimal
    target_amounts: Mapping[str, Decimal]
    theoretical_target_quantities: Mapping[str, int]


class RebalancePlanner:
    def __init__(self, fee_estimator: Callable[[str, OrderSide, int, Decimal], Decimal]) -> None:
        self._fee = fee_estimator

    def plan(
        self,
        target: TargetPortfolio,
        *,
        current_positions: Mapping[str, int],
        sellable_quantities: Mapping[str, int],
        portfolio_value: Decimal,
        reference_prices: Mapping[str, Decimal],
        lot_rules: Mapping[str, LotRule],
        cash_budget: Decimal,
    ) -> RebalancePlan:
        target_weights = {item.security_id: item.target_weight for item in target.positions}
        target_ranks = {item.security_id: item.rank for item in target.positions}
        amounts: dict[str, Decimal] = {}
        quantities: dict[str, int] = {}
        for security_id, weight in target_weights.items():
            price = reference_prices.get(security_id)
            rule = lot_rules.get(security_id)
            if price is None or price <= 0 or rule is None:
                continue
            amounts[security_id] = quantize_fen(portfolio_value * weight)
            lots = (amounts[security_id] / price / rule.buy_lot_size).to_integral_value(
                rounding=ROUND_FLOOR
            )
            quantities[security_id] = int(lots) * rule.buy_lot_size
        sells = []
        for security_id, current in current_positions.items():
            target_quantity = quantities.get(security_id, 0)
            if current <= target_quantity or security_id not in reference_prices:
                continue
            rule = lot_rules[security_id]
            available = min(current, sellable_quantities.get(security_id, 0))
            desired = current - target_quantity
            requested = (
                available
                if target_quantity == 0
                else min(
                    (desired // rule.sell_lot_size) * rule.sell_lot_size,
                    (available // rule.sell_lot_size) * rule.sell_lot_size,
                )
            )
            if requested > 0:
                sells.append(
                    RebalanceInstruction(
                        security_id,
                        OrderSide.SELL,
                        current,
                        target_quantity,
                        requested,
                        rule.sell_lot_size,
                        0,
                        reference_prices[security_id],
                        ("TARGET_EXIT" if target_quantity == 0 else "TARGET_DECREASE",),
                    )
                )
        sells.sort(
            key=lambda item: (0 if item.theoretical_target_quantity == 0 else 1, item.security_id)
        )
        buy_candidates = []
        for security_id, target_quantity in quantities.items():
            current = current_positions.get(security_id, 0)
            if target_quantity > current:
                gap = amounts[security_id] - Decimal(current) * reference_prices[security_id]
                buy_candidates.append(
                    (
                        gap / portfolio_value,
                        target_ranks[security_id] or 10**9,
                        security_id,
                        target_quantity - current,
                    )
                )
        buy_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        buys = []
        cash = cash_budget
        for _, _, security_id, desired in buy_candidates:
            rule = lot_rules[security_id]
            price = reference_prices[security_id]
            requested = self._affordable(security_id, desired, rule.buy_lot_size, price, cash)
            if requested:
                cash -= Decimal(requested) * price + self._fee(
                    security_id, OrderSide.BUY, requested, price
                )
                buys.append(
                    RebalanceInstruction(
                        security_id,
                        OrderSide.BUY,
                        current_positions.get(security_id, 0),
                        quantities[security_id],
                        requested,
                        rule.buy_lot_size,
                        1,
                        price,
                        ("TARGET_INCREASE", "LOT_ROUNDING_RESIDUAL"),
                    )
                )
        return RebalancePlan(tuple(sells + buys), quantize_fen(cash), amounts, quantities)

    def _affordable(
        self, security_id: str, desired: int, lot_size: int, price: Decimal, cash: Decimal
    ) -> int:
        high = desired // lot_size
        low = 0
        while low < high:
            middle = (low + high + 1) // 2
            quantity = middle * lot_size
            cost = Decimal(quantity) * price + self._fee(
                security_id, OrderSide.BUY, quantity, price
            )
            if cost <= cash:
                low = middle
            else:
                high = middle - 1
        return low * lot_size
