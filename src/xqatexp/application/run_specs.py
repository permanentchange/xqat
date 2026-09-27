from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from xqatexp.domain.contracts import ResolvedRunContext
from xqatexp.domain.enums import RunMode


@dataclass(frozen=True, slots=True)
class BacktestRunSpec:
    context: ResolvedRunContext
    start_date: date
    end_date: date

    @classmethod
    def from_context(cls, context: ResolvedRunContext) -> BacktestRunSpec:
        if (
            context.mode is not RunMode.BACKTEST
            or context.start_date is None
            or context.end_date is None
        ):
            raise ValueError("CONFIG_VALUE_INVALID: backtest dates required")
        return cls(context, context.start_date, context.end_date)


@dataclass(frozen=True, slots=True)
class DailyDecisionRunSpec:
    context: ResolvedRunContext
    decision_date: date

    @classmethod
    def from_context(cls, context: ResolvedRunContext) -> DailyDecisionRunSpec:
        if context.mode is RunMode.BACKTEST or context.decision_date is None:
            raise ValueError("CONFIG_VALUE_INVALID: decision_date required")
        return cls(context, context.decision_date)
