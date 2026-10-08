from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from xqatexp.backtest.account_events import (
    AccountEvent,
    CashInitialized,
    SplitApplied,
    StockDistributionApplied,
    TradeFilled,
    ValuationRecorded,
)
from xqatexp.domain.enums import OrderSide


@dataclass(frozen=True, slots=True)
class StrategyPositionState:
    security_id: str
    quantity: int = 0
    remaining_cost_basis: Decimal = Decimal("0")
    last_trade_side: OrderSide | None = None
    last_trade_date: date | None = None
    last_trade_quantity: int = 0
    last_trade_price: Decimal | None = None
    last_buy_price: Decimal | None = None
    cumulative_buy_notional: Decimal = Decimal("0")
    exit_base_quantity: Decimal | None = None
    exit_sold_quantity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.exit_sold_quantity.is_finite() or self.exit_sold_quantity < 0:
            raise ValueError("STRATEGY_STATE_INVALID: invalid exit sold quantity")
        if self.exit_base_quantity is None:
            if self.exit_sold_quantity != 0:
                raise ValueError("STRATEGY_STATE_INVALID: exit sold quantity without base")
        elif (
            not self.exit_base_quantity.is_finite()
            or self.exit_base_quantity <= 0
            or self.exit_base_quantity - self.exit_sold_quantity != self.quantity
        ):
            raise ValueError("STRATEGY_STATE_INVALID: exit quantities do not match position")

    @property
    def average_cost(self) -> Decimal | None:
        if self.quantity <= 0:
            return None
        return self.remaining_cost_basis / Decimal(self.quantity)


@dataclass(frozen=True, slots=True)
class StrategyStateSnapshot:
    schema_version: str
    strategy_id: str
    strategy_version: str
    initial_capital: Decimal
    as_of: date | None
    positions: tuple[StrategyPositionState, ...]


@dataclass(frozen=True, slots=True)
class StrategyStateView:
    snapshot: StrategyStateSnapshot

    @property
    def initial_capital(self) -> Decimal:
        return self.snapshot.initial_capital

    @property
    def as_of(self) -> date | None:
        return self.snapshot.as_of

    def position(self, security_id: str) -> StrategyPositionState | None:
        return next(
            (item for item in self.snapshot.positions if item.security_id == security_id),
            None,
        )


class StrategyStateReducer:
    def __init__(self, strategy_id: str, strategy_version: str) -> None:
        self._strategy_id = strategy_id
        self._strategy_version = strategy_version

    def initial(self, initial_capital: Decimal) -> StrategyStateSnapshot:
        if initial_capital < 0:
            raise ValueError("STRATEGY_STATE_INVALID: negative initial capital")
        return StrategyStateSnapshot(
            "1.0",
            self._strategy_id,
            self._strategy_version,
            initial_capital,
            None,
            (),
        )

    def replay(
        self,
        initial_capital: Decimal,
        events: tuple[AccountEvent, ...],
    ) -> StrategyStateSnapshot:
        state = self.initial(initial_capital)
        for event in events:
            state = self.apply(state, event)
        return state

    def apply(
        self,
        state: StrategyStateSnapshot,
        event: AccountEvent,
    ) -> StrategyStateSnapshot:
        self._validate_identity(state)
        if isinstance(event, CashInitialized):
            if event.amount != state.initial_capital:
                raise ValueError("STRATEGY_STATE_INVALID: initial capital mismatch")
            return state
        if isinstance(event, TradeFilled):
            return self._apply_trade(state, event)
        if isinstance(event, StockDistributionApplied):
            return self._apply_quantity_adjustment(
                state,
                event.security_id,
                event.quantity,
            )
        if isinstance(event, SplitApplied):
            return self._apply_quantity_adjustment(
                state,
                event.security_id,
                event.quantity,
            )
        if isinstance(event, ValuationRecorded):
            return replace(state, as_of=event.valuation_date)
        return state

    def _apply_trade(
        self,
        state: StrategyStateSnapshot,
        event: TradeFilled,
    ) -> StrategyStateSnapshot:
        record = event.record
        if state.as_of is not None and record.execution_date < state.as_of:
            raise ValueError("STRATEGY_STATE_INVALID: execution date precedes state as_of")
        if record.filled_quantity <= 0:
            return state
        positions = self._positions(state)
        current = positions.get(record.security_id) or StrategyPositionState(record.security_id)

        if record.side is OrderSide.BUY:
            next_quantity = current.quantity + record.filled_quantity
            next_basis = current.remaining_cost_basis + record.gross_amount + record.fees.total
            next_state = replace(
                current,
                quantity=next_quantity,
                remaining_cost_basis=next_basis,
                last_trade_side=record.side,
                last_trade_date=record.execution_date,
                last_trade_quantity=record.filled_quantity,
                last_trade_price=record.execution_price,
                last_buy_price=record.execution_price,
                exit_base_quantity=None,
                exit_sold_quantity=Decimal("0"),
                cumulative_buy_notional=(
                    record.gross_amount
                    if current.quantity == 0
                    else current.cumulative_buy_notional + record.gross_amount
                ),
            )
        else:
            if record.filled_quantity > current.quantity:
                raise ValueError("STRATEGY_STATE_INVALID: sell exceeds tracked quantity")
            basis_per_share = (
                current.remaining_cost_basis / Decimal(current.quantity)
                if current.quantity
                else Decimal("0")
            )
            next_quantity = current.quantity - record.filled_quantity
            next_basis = (
                Decimal("0")
                if next_quantity == 0
                else current.remaining_cost_basis
                - basis_per_share * Decimal(record.filled_quantity)
            )
            exit_base = current.exit_base_quantity
            if exit_base is None and current.last_trade_side is not OrderSide.SELL:
                exit_base = Decimal(current.quantity)
            exit_sold = (
                current.exit_sold_quantity + record.filled_quantity
                if exit_base is not None
                else Decimal("0")
            )
            if next_quantity == 0:
                exit_base, exit_sold = None, Decimal("0")
            next_state = replace(
                current,
                quantity=next_quantity,
                remaining_cost_basis=next_basis,
                last_trade_side=record.side,
                last_trade_date=record.execution_date,
                last_trade_quantity=record.filled_quantity,
                last_trade_price=record.execution_price,
                exit_base_quantity=exit_base,
                exit_sold_quantity=exit_sold,
            )

        positions[record.security_id] = next_state
        return replace(
            state,
            as_of=record.execution_date,
            positions=self._sorted_positions(positions),
        )

    def _apply_quantity_adjustment(
        self,
        state: StrategyStateSnapshot,
        security_id: str,
        quantity: int,
    ) -> StrategyStateSnapshot:
        if quantity < 0:
            raise ValueError("STRATEGY_STATE_INVALID: negative quantity adjustment")
        positions = self._positions(state)
        current = positions.get(security_id) or StrategyPositionState(security_id)
        next_quantity = current.quantity + quantity
        adjusted_last_buy_price = current.last_buy_price
        exit_base = current.exit_base_quantity
        exit_sold = current.exit_sold_quantity
        if exit_base is not None and current.quantity > 0:
            # Fractional historical quantities are corporate-action equivalents, not fills.
            exit_sold = (exit_sold * Decimal(next_quantity) / Decimal(current.quantity)).quantize(
                Decimal("0.000000000001")
            )
            exit_base = Decimal(next_quantity) + exit_sold
        if adjusted_last_buy_price is not None and current.quantity > 0 and next_quantity > 0:
            adjusted_last_buy_price = (
                adjusted_last_buy_price * Decimal(current.quantity) / Decimal(next_quantity)
            )
        positions[security_id] = replace(
            current,
            quantity=next_quantity,
            last_buy_price=adjusted_last_buy_price,
            exit_base_quantity=exit_base,
            exit_sold_quantity=exit_sold,
        )
        return replace(state, positions=self._sorted_positions(positions))

    def _validate_identity(self, state: StrategyStateSnapshot) -> None:
        if (
            state.strategy_id != self._strategy_id
            or state.strategy_version != self._strategy_version
        ):
            raise ValueError("STRATEGY_STATE_INVALID: strategy identity mismatch")

    @staticmethod
    def _positions(
        state: StrategyStateSnapshot,
    ) -> dict[str, StrategyPositionState]:
        return {item.security_id: item for item in state.positions}

    @staticmethod
    def _sorted_positions(
        positions: dict[str, StrategyPositionState],
    ) -> tuple[StrategyPositionState, ...]:
        return tuple(positions[key] for key in sorted(positions))
