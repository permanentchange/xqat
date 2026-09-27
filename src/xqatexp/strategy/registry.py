from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from xqatexp.domain.contracts import (
    CustomFactorView,
    ResearchDataView,
    StrategyDeclaration,
    TargetPortfolio,
)
from xqatexp.strategy.declaration import strategy_declaration
from xqatexp.strategy.schedule import DecisionSchedule, WeeklyLastTradingDayCloseSchedule
from xqatexp.strategy.weekly_parameters import normalize_weekly_parameters
from xqatexp.strategy.weekly_strategy import WeeklyMarketGuardRankStrategy


class RegisteredStrategy(Protocol):
    def generate_target(
        self,
        research: ResearchDataView,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
    ) -> TargetPortfolio: ...


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    strategy_version: str
    declaration_factory: Callable[[Mapping[str, object]], StrategyDeclaration]
    strategy_factory: Callable[[Mapping[str, object]], RegisteredStrategy]
    parameter_normalizer: Callable[[Mapping[str, object], Mapping[str, object]], dict[str, object]]
    schedule: DecisionSchedule

    def declaration(self, parameters: Mapping[str, object]) -> StrategyDeclaration:
        declaration = self.declaration_factory(parameters)
        if (
            declaration.strategy_id != self.strategy_id
            or declaration.strategy_version != self.strategy_version
        ):
            raise ValueError("STRATEGY_REGISTRY_INCONSISTENT: declaration identity mismatch")
        return declaration

    def create(self, parameters: Mapping[str, object]) -> RegisteredStrategy:
        return self.strategy_factory(parameters)

    def normalize_parameters(
        self, raw: Mapping[str, object], cli: Mapping[str, object]
    ) -> dict[str, object]:
        return self.parameter_normalizer(raw, cli)


_WEEKLY = StrategySpec(
    strategy_id="weekly_market_guard_rank_v1",
    strategy_version="1.0.0",
    declaration_factory=strategy_declaration,
    strategy_factory=WeeklyMarketGuardRankStrategy,
    parameter_normalizer=normalize_weekly_parameters,
    schedule=WeeklyLastTradingDayCloseSchedule(),
)

_SPECS = {(_WEEKLY.strategy_id, _WEEKLY.strategy_version): _WEEKLY}


def resolve_strategy_spec(strategy_id: str, strategy_version: str) -> StrategySpec:
    try:
        return _SPECS[(strategy_id, strategy_version)]
    except KeyError as error:
        raise ValueError(
            f"STRATEGY_UNSUPPORTED: {strategy_id}@{strategy_version}"
        ) from error
