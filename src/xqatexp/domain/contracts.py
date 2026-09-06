from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from xqatexp.domain.enums import (
    AccountScope,
    AccountScopeCompleteness,
    AssetType,
    FillStatus,
    MarketRegime,
    OrderSide,
    PositionCompleteness,
    RunMode,
    TargetTransition,
    TradeAction,
    UnfilledReason,
)


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class CustomFactorInput:
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class ResolvedRunContext:
    run_id: str
    mode: RunMode
    strategy_id: str
    strategy_version: str
    parameters: Mapping[str, object]
    research_artifact_path: Path
    research_artifact_sha256: str
    custom_factor_inputs: tuple[CustomFactorInput, ...]
    output_path: Path
    execution_assumptions: Mapping[str, object]
    generated_at: datetime
    decision_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    account_snapshot_path: Path | None = None
    previous_target_path: Path | None = None


@dataclass(frozen=True, slots=True)
class DataRequirement:
    requirement_id: str
    dataset: str
    fields: tuple[str, ...]
    date_range: DateRange
    security_scope: str
    required: bool
    missing_policy: str


@dataclass(frozen=True, slots=True)
class StrategyDeclaration:
    strategy_id: str
    strategy_version: str
    parameter_schema_version: str
    decision_frequency: str
    lookback_trade_days: int
    required_fields: tuple[str, ...]
    required_system_factors: tuple[str, ...]
    required_custom_factors: tuple[str, ...]
    missing_policies: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class SecuritySnapshot:
    security_id: str
    asset_type: AssetType
    values: Mapping[str, object]


class ResearchDataSlice(Protocol):
    @property
    def as_of_date(self) -> date: ...

    def universe(self) -> Sequence[SecuritySnapshot]: ...

    def history(
        self,
        security_ids: Sequence[str],
        fields: Sequence[str],
        start: date,
        end: date,
    ) -> object: ...

    def system_factors(self, factor_ids: Sequence[str], security_ids: Sequence[str]) -> object: ...

    def benchmark_history(self, fields: Sequence[str], start: date, end: date) -> object: ...


class ResearchDataView(Protocol):
    @property
    def decision_date(self) -> date: ...

    @property
    def earliest_date(self) -> date: ...

    def trading_days(self, start: date, end: date) -> Sequence[date]: ...

    def slice(self, as_of_date: date) -> ResearchDataSlice: ...


class CustomFactorView(Protocol):
    @property
    def decision_date(self) -> date: ...

    def values_at(
        self,
        factor_date: date,
        factor_ids: Sequence[str],
        security_ids: Sequence[str],
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class Explanation:
    code: str
    message: str
    values: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TargetPosition:
    security_id: str
    asset_type: AssetType
    target_weight: Decimal
    rank: int | None
    score: float | None
    transition: TargetTransition | None
    explanation_codes: tuple[str, ...]
    holding_age_weeks: int | None


@dataclass(frozen=True, slots=True)
class TargetTransitionRecord:
    security_id: str
    previous_weight: Decimal
    current_weight: Decimal
    transition: TargetTransition
    explanation_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TargetPortfolio:
    strategy_id: str
    strategy_version: str
    decision_date: date
    effective_from: date
    market_regime: MarketRegime
    theoretical_drawdown: Decimal
    drawdown_window_trade_days: int
    drawdown_observations: int
    drawdown_overlay_level: str
    positions: tuple[TargetPosition, ...]
    transition_records: tuple[TargetTransitionRecord, ...]
    cash_weight: Decimal
    explanations: tuple[Explanation, ...]


@dataclass(frozen=True, slots=True)
class AccountPosition:
    security_id: str
    quantity: int
    sellable_quantity: int | None = None
    market_value: Decimal | None = None
    reference_price: Decimal | None = None
    reference_price_date: date | None = None


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    schema_version: str
    as_of: datetime
    currency: str
    account_scope: AccountScope
    scope_completeness: AccountScopeCompleteness
    positions_completeness: PositionCompleteness
    available_cash: Decimal | None
    managed_total_assets: Decimal | None
    excluded_asset_value: Decimal | None
    source_note: str
    positions: tuple[AccountPosition, ...]


@dataclass(frozen=True, slots=True)
class RebalanceInstruction:
    security_id: str
    side: OrderSide
    current_quantity: int
    theoretical_target_quantity: int
    requested_quantity: int
    lot_size: int
    priority: int
    reference_price: Decimal
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutionFees:
    commission: Decimal
    transfer_fee: Decimal
    stamp_duty: Decimal

    @property
    def total(self) -> Decimal:
        return self.commission + self.transfer_fee + self.stamp_duty


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    execution_date: date
    security_id: str
    side: OrderSide
    requested_quantity: int
    filled_quantity: int
    execution_price: Decimal
    gross_amount: Decimal
    fees: ExecutionFees
    status: FillStatus
    unfilled_reason: UnfilledReason | None = None
    reference_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class TradeAdviceItem:
    security_id: str
    action: TradeAction
    current_quantity: int | None
    target_weight: Decimal
    target_amount: Decimal | None
    theoretical_target_quantity: int | None
    suggested_quantity: int | None
    max_confirmed_sell_quantity: int | None
    unresolved_quantity: int | None
    reference_price: Decimal | None
    reference_price_date: date | None
    reason_codes: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TradeAdvice:
    decision_date: date
    effective_from: date
    items: tuple[TradeAdviceItem, ...]
    issues: tuple[Any, ...]
    limitations: tuple[str, ...]
    non_order_disclaimer: str
