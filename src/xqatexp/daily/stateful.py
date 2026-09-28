from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast

from xqatexp.domain.contracts import AccountSnapshot, CustomFactorView
from xqatexp.domain.enums import PositionCompleteness
from xqatexp.strategy.intents import TradeIntentDecision
from xqatexp.strategy.state import StrategyStateSnapshot, StrategyStateView


class StatefulDailyStrategy(Protocol):
    def generate_stateful_decision(
        self,
        research: Any,
        state: StrategyStateView,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
    ) -> TradeIntentDecision: ...


class StatefulDailyDecisionService:
    def run(
        self,
        strategy: StatefulDailyStrategy,
        research: Any,
        state: StrategyStateSnapshot,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
        *,
        account: AccountSnapshot | None,
    ) -> TradeIntentDecision:
        decision_date = research.decision_date
        if state.as_of is not None and state.as_of > decision_date:
            raise ValueError("STRATEGY_STATE_INVALID: state is newer than decision date")
        self._check_account_consistency(state, account)
        decision = cast(
            TradeIntentDecision,
            strategy.generate_stateful_decision(
                research,
                StrategyStateView(state),
                custom,
                parameters,
            ),
        )
        if (
            decision.strategy_id != state.strategy_id
            or decision.strategy_version != state.strategy_version
        ):
            raise ValueError("STRATEGY_STATE_INVALID: decision and state identity mismatch")
        if decision.decision_date != decision_date:
            raise ValueError("STRATEGY_INTENT_INVALID: decision date mismatch")
        if decision.effective_from <= decision.decision_date:
            raise ValueError("STRATEGY_INTENT_INVALID: decision is not forward effective")
        intent_ids = [item.intent_id for item in decision.intents]
        if len(intent_ids) != len(set(intent_ids)):
            raise ValueError("STRATEGY_INTENT_INVALID: duplicate intent id")
        return decision

    @staticmethod
    def _check_account_consistency(
        state: StrategyStateSnapshot,
        account: AccountSnapshot | None,
    ) -> None:
        if account is None:
            return
        state_positions = {item.security_id: item.quantity for item in state.positions}
        account_positions = {item.security_id: item.quantity for item in account.positions}
        for security_id in set(state_positions) & set(account_positions):
            if state_positions[security_id] != account_positions[security_id]:
                raise ValueError(
                    "STRATEGY_STATE_ACCOUNT_CONFLICT: "
                    f"{security_id} state={state_positions[security_id]} "
                    f"account={account_positions[security_id]}"
                )
        if account.positions_completeness is PositionCompleteness.COMPLETE:
            positive_state = {key: value for key, value in state_positions.items() if value > 0}
            positive_account = {key: value for key, value in account_positions.items() if value > 0}
            if positive_state != positive_account:
                raise ValueError(
                    "STRATEGY_STATE_ACCOUNT_CONFLICT: complete account positions differ from state"
                )
