from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from xqatexp.domain.contracts import StrategyDeclaration
from xqatexp.strategy.schedule import (
    DailyCloseSchedule,
    DecisionSchedule,
    WeeklyLastTradingDayCloseSchedule,
)
from xqatexp.strategy.strategies.staged_drawdown_v1.declaration import (
    staged_drawdown_declaration,
)
from xqatexp.strategy.strategies.staged_drawdown_v1.parameters import (
    normalize_staged_drawdown_parameters,
)
from xqatexp.strategy.strategies.staged_drawdown_v1.strategy import StagedDrawdownStrategy
from xqatexp.strategy.strategies.weekly_market_guard_rank_v1.declaration import (
    strategy_declaration,
)
from xqatexp.strategy.strategies.weekly_market_guard_rank_v1.parameters import (
    normalize_weekly_parameters,
)
from xqatexp.strategy.strategies.weekly_market_guard_rank_v1.strategy import (
    WeeklyMarketGuardRankStrategy,
)


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    strategy_version: str
    declaration_factory: Callable[[Mapping[str, object]], StrategyDeclaration]
    strategy_factory: Callable[[Mapping[str, object]], Any]
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

    def create(self, parameters: Mapping[str, object]) -> Any:
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

_STAGED_DRAWDOWN = StrategySpec(
    strategy_id="staged_drawdown_v1",
    strategy_version="1.0.0",
    declaration_factory=staged_drawdown_declaration,
    strategy_factory=StagedDrawdownStrategy,
    parameter_normalizer=normalize_staged_drawdown_parameters,
    schedule=DailyCloseSchedule(),
)

_SPECS = {
    (_WEEKLY.strategy_id, _WEEKLY.strategy_version): _WEEKLY,
    (_STAGED_DRAWDOWN.strategy_id, _STAGED_DRAWDOWN.strategy_version): _STAGED_DRAWDOWN,
}


def resolve_strategy_spec(strategy_id: str, strategy_version: str) -> StrategySpec:
    try:
        return _SPECS[(strategy_id, strategy_version)]
    except KeyError as error:
        raise ValueError(f"STRATEGY_UNSUPPORTED: {strategy_id}@{strategy_version}") from error
