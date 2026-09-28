from __future__ import annotations

from datetime import date
from decimal import Decimal

from xqatexp.backtest.account import SimulatedAccount
from xqatexp.backtest.decision_executor import (
    AllocationDecisionExecutor,
    DecisionExecutionResult,
    ExecutionData,
)
from xqatexp.backtest.fees import FeeModel
from xqatexp.backtest.intent_executor import IntentExecutor
from xqatexp.domain.contracts import AllocationDecision
from xqatexp.strategy.intents import StrategyDecision, TradeIntentDecision
from xqatexp.strategy.state import StrategyStateView


class DecisionExecutorRouter:
    def __init__(self, fees: FeeModel | None = None) -> None:
        model = fees or FeeModel.default()
        self._allocation = AllocationDecisionExecutor(model)
        self._intent = IntentExecutor(model)

    def execute(
        self,
        *,
        data: ExecutionData,
        account: SimulatedAccount,
        decision: StrategyDecision,
        state: StrategyStateView | None,
        execution_date: date,
        slippage: Decimal,
        participation: Decimal,
    ) -> DecisionExecutionResult:
        if isinstance(decision, AllocationDecision):
            return self._allocation.execute(
                data=data,
                account=account,
                decision=decision,
                execution_date=execution_date,
                slippage=slippage,
                participation=participation,
            )
        if isinstance(decision, TradeIntentDecision):
            if state is None:
                raise ValueError("STRATEGY_STATE_REQUIRED: intent decision needs confirmed state")
            return self._intent.execute(
                data=data,
                account=account,
                decision=decision,
                state=state,
                execution_date=execution_date,
                slippage=slippage,
                participation=participation,
            )
        raise TypeError(f"unsupported strategy decision: {type(decision)!r}")
