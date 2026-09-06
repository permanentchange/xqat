from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal

from xqatexp.backtest.fees import FeeModel
from xqatexp.domain.contracts import (
    AccountPosition,
    AccountSnapshot,
    TargetPortfolio,
    TradeAdvice,
    TradeAdviceItem,
)
from xqatexp.domain.enums import (
    AccountScopeCompleteness,
    AssetType,
    OrderSide,
    PositionCompleteness,
    Severity,
    TradeAction,
)
from xqatexp.domain.issues import Issue
from xqatexp.portfolio.rebalance import LotRule

_ACTION_ORDER = {
    TradeAction.SELL: 0,
    TradeAction.DECREASE: 1,
    TradeAction.BUY: 2,
    TradeAction.INCREASE: 3,
    TradeAction.HOLD: 4,
    TradeAction.UNRESOLVED: 5,
}


@dataclass(slots=True)
class _Draft:
    security_id: str
    asset_type: AssetType | None
    weight: Decimal
    price: Decimal | None
    rule: LotRule | None
    amount: Decimal | None
    theoretical: int | None
    position: AccountPosition | None
    current: int | None
    action: TradeAction
    limitations: set[str]


class DailyAdviceService:
    def __init__(self, fees: FeeModel | None = None) -> None:
        self._fees = fees or FeeModel.default()

    def run(
        self,
        *,
        target: TargetPortfolio,
        account: AccountSnapshot | None,
        reference_prices: Mapping[str, Decimal],
        lot_rules: Mapping[str, LotRule],
        run_started_at: datetime,
    ) -> TradeAdvice:
        positions = (
            {} if account is None else {item.security_id: item for item in account.positions}
        )
        managed, account_limitations, issues = self._account_facts(account, run_started_at)
        all_ids = sorted(set(positions) | {item.security_id for item in target.positions})
        target_by_id = {item.security_id: item for item in target.positions}
        drafts: list[_Draft] = []
        for security_id in all_ids:
            desired = target_by_id.get(security_id)
            weight = desired.target_weight if desired is not None else Decimal("0")
            price = reference_prices.get(security_id)
            rule = lot_rules.get(security_id)
            limitations = set(account_limitations)
            amount = managed * weight if managed is not None else None
            theoretical = None
            if amount is not None and price is not None and rule is not None:
                theoretical = self._lot_quantity(amount, price, rule.buy_lot_size)
            elif price is None:
                limitations.add("REFERENCE_PRICE_UNKNOWN")
            current_position = positions.get(security_id)
            if account is not None and (
                current_position is not None
                or account.positions_completeness is PositionCompleteness.COMPLETE
            ):
                current = current_position.quantity if current_position is not None else 0
            else:
                current = None
            action = self._action(current, theoretical, weight)
            drafts.append(
                _Draft(
                    security_id,
                    desired.asset_type if desired is not None else None,
                    weight,
                    price,
                    rule,
                    amount,
                    theoretical,
                    current_position,
                    current,
                    action,
                    limitations,
                )
            )

        spendable = self._spendable(account, managed, target.cash_weight)
        buy_budget = spendable
        candidates = sorted(
            (draft for draft in drafts if draft.action in (TradeAction.BUY, TradeAction.INCREASE)),
            key=lambda item: (
                -self._relative_gap(item, managed),
                item.security_id,
            ),
        )
        buy_suggestions: dict[str, int | None] = {}
        for draft in candidates:
            security_id = draft.security_id
            if buy_budget is None:
                buy_suggestions[security_id] = None
                draft.limitations.add("AVAILABLE_CASH_UNKNOWN")
                continue
            if draft.theoretical is None or draft.current is None:
                buy_suggestions[security_id] = None
                continue
            desired_quantity = draft.theoretical - draft.current
            if draft.price is None or draft.rule is None or draft.asset_type is None:
                buy_suggestions[security_id] = None
                continue
            affordable = self._affordable(
                desired_quantity,
                draft.rule.buy_lot_size,
                draft.price,
                buy_budget,
                draft.asset_type,
                target.effective_from,
            )
            buy_suggestions[security_id] = affordable
            fees = self._fees.calculate(
                draft.asset_type,
                OrderSide.BUY,
                Decimal(affordable) * draft.price,
                target.effective_from,
            ).total
            buy_budget -= Decimal(affordable) * draft.price + fees

        items = [
            self._item(draft, buy_suggestions.get(draft.security_id), target) for draft in drafts
        ]
        items.sort(key=lambda item: (_ACTION_ORDER[item.action], item.security_id))
        for item in items:
            if "REFERENCE_PRICE_UNKNOWN" in item.limitations:
                issues.append(
                    Issue(
                        "ADVICE_REFERENCE_PRICE_MISSING",
                        Severity.WARNING,
                        "DAILY_ADVICE",
                        "SECURITY",
                        "缺少证券参考价格; 无法计算精确数量。",
                        security_id=item.security_id,
                        field="reference_price",
                    )
                )
            if "SELLABLE_QUANTITY_UNKNOWN" in item.limitations:
                issues.append(
                    Issue(
                        "ADVICE_SELLABLE_UNKNOWN",
                        Severity.WARNING,
                        "DAILY_ADVICE",
                        "SECURITY",
                        "可卖数量未知; 不生成卖出数量建议。",
                        security_id=item.security_id,
                        field="sellable_quantity",
                    )
                )
        global_limitations = tuple(sorted({value for item in items for value in item.limitations}))
        issues.sort(key=lambda item: (item.code, item.security_id or "", item.field or ""))
        return TradeAdvice(
            target.decision_date,
            target.effective_from,
            tuple(items),
            tuple(issues),
            global_limitations,
            "仅供研究参考; 不是订单或投资承诺。",
        )

    def _item(
        self, draft: _Draft, buy_suggestion: int | None, target: TargetPortfolio
    ) -> TradeAdviceItem:
        security_id = draft.security_id
        current = draft.current
        theoretical = draft.theoretical
        position = draft.position
        action = draft.action
        limitations = draft.limitations
        suggested: int | None = None
        max_sell: int | None = None
        unresolved: int | None = None
        reasons: set[str] = set()
        if action in (TradeAction.BUY, TradeAction.INCREASE):
            suggested = buy_suggestion
            if suggested is not None and theoretical is not None and current is not None:
                unresolved = max(int(theoretical) - int(current) - suggested, 0)
                if unresolved:
                    reasons.add("CASH_BUDGET_LIMITED")
        elif action in (TradeAction.SELL, TradeAction.DECREASE):
            assert position is not None
            if position.sellable_quantity is None:
                limitations.add("SELLABLE_QUANTITY_UNKNOWN")
            elif theoretical is not None and current is not None:
                desired = int(current) - int(theoretical)
                rule = draft.rule
                if rule is not None:
                    suggested = (
                        min(desired, position.sellable_quantity)
                        if int(theoretical) == 0
                        else min(desired, position.sellable_quantity)
                        // rule.sell_lot_size
                        * rule.sell_lot_size
                    )
                    max_sell = position.sellable_quantity
                    unresolved = desired - suggested
                    if unresolved:
                        reasons.add("SELLABLE_LIMITED")
        elif action is TradeAction.HOLD:
            suggested = 0
            unresolved = 0
            reasons.add("TARGET_RETAIN")
        else:
            reasons.add("ACCOUNT_FACTS_INCOMPLETE")
        if draft.price is None:
            reasons.add("REFERENCE_PRICE_MISSING")
        return TradeAdviceItem(
            security_id,
            action,
            None if current is None else int(current),
            draft.weight,
            draft.amount,
            None if theoretical is None else int(theoretical),
            suggested,
            max_sell,
            unresolved,
            draft.price,
            target.decision_date if draft.price is not None else None,
            tuple(sorted(reasons)),
            tuple(sorted(limitations)),
        )

    @staticmethod
    def _action(
        current: int | None, theoretical: int | None, target_weight: Decimal
    ) -> TradeAction:
        if current is None or theoretical is None:
            return TradeAction.UNRESOLVED
        current_value, target_value = int(current), int(theoretical)
        if target_value > current_value:
            return TradeAction.BUY if current_value == 0 else TradeAction.INCREASE
        if target_value < current_value:
            return TradeAction.SELL if target_weight == 0 else TradeAction.DECREASE
        return TradeAction.HOLD

    @staticmethod
    def _account_facts(
        account: AccountSnapshot | None, run_started_at: datetime
    ) -> tuple[Decimal | None, set[str], list[Issue]]:
        if account is None:
            issue = Issue(
                "ADVICE_ACCOUNT_MISSING",
                Severity.WARNING,
                "DAILY_ADVICE",
                "ACCOUNT",
                "未提供账户快照; 只输出策略目标。",
            )
            return None, {"ACCOUNT_NOT_PROVIDED"}, [issue]
        limitations: set[str] = set()
        issues: list[Issue] = []
        if account.positions_completeness is PositionCompleteness.UNKNOWN:
            limitations.add("POSITIONS_UNKNOWN")
            issues.append(
                Issue(
                    "ADVICE_HOLDINGS_UNKNOWN",
                    Severity.WARNING,
                    "DAILY_ADVICE",
                    "ACCOUNT",
                    "当前持仓未知。",
                )
            )
        elif account.positions_completeness is PositionCompleteness.PARTIAL:
            limitations.add("POSITIONS_PARTIAL")
            issues.append(
                Issue(
                    "ADVICE_HOLDINGS_PARTIAL",
                    Severity.WARNING,
                    "DAILY_ADVICE",
                    "ACCOUNT",
                    "当前持仓仅部分提供。",
                )
            )
        managed = account.managed_total_assets
        if account.scope_completeness is not AccountScopeCompleteness.COMPLETE:
            managed = None
            limitations.add("ACCOUNT_SCOPE_INCOMPLETE")
            issues.append(
                Issue(
                    "ADVICE_SCOPE_INCOMPLETE",
                    Severity.WARNING,
                    "DAILY_ADVICE",
                    "ACCOUNT",
                    "策略管理资产范围不完整。",
                )
            )
        derived: Decimal | None = None
        if DailyAdviceService._can_derive(account):
            assert account.available_cash is not None
            derived = account.available_cash + sum(
                (
                    position.market_value
                    for position in account.positions
                    if position.market_value is not None
                ),
                Decimal("0"),
            )
        if managed is None and derived is not None:
            managed = derived
        elif managed is not None and derived is not None:
            tolerance = max(Decimal("1"), managed * Decimal("0.001"))
            if abs(managed - derived) > tolerance:
                limitations.add("ACCOUNT_VALUE_CONFLICT")
                issues.append(
                    Issue(
                        "ADVICE_ACCOUNT_VALUE_CONFLICT",
                        Severity.WARNING,
                        "DAILY_ADVICE",
                        "ACCOUNT",
                        "账户总资产与完整分项不一致。",
                    )
                )
        if managed is None:
            limitations.add("MANAGED_TOTAL_ASSETS_UNKNOWN")
        if account.available_cash is None:
            limitations.add("AVAILABLE_CASH_UNKNOWN")
            issues.append(
                Issue(
                    "ADVICE_CASH_UNKNOWN",
                    Severity.WARNING,
                    "DAILY_ADVICE",
                    "ACCOUNT",
                    "可用现金未知。",
                )
            )
        if account.excluded_asset_value is not None and account.excluded_asset_value > 0:
            issues.append(
                Issue(
                    "ADVICE_OUT_OF_SCOPE_ASSET_PRESENT",
                    Severity.WARNING,
                    "DAILY_ADVICE",
                    "ACCOUNT",
                    "存在策略管理范围外资产; 未计入建议。",
                )
            )
        if (run_started_at.date() - account.as_of.date()).days > 3:
            limitations.add("ACCOUNT_STALE")
            issues.append(
                Issue(
                    "ADVICE_ACCOUNT_STALE",
                    Severity.WARNING,
                    "DAILY_ADVICE",
                    "ACCOUNT",
                    "账户快照超过三天。",
                )
            )
        return managed, limitations, issues

    @staticmethod
    def _can_derive(account: AccountSnapshot) -> bool:
        return (
            account.scope_completeness is AccountScopeCompleteness.COMPLETE
            and account.positions_completeness is PositionCompleteness.COMPLETE
            and account.available_cash is not None
            and all(position.market_value is not None for position in account.positions)
        )

    @staticmethod
    def _spendable(
        account: AccountSnapshot | None, managed: Decimal | None, cash_weight: Decimal
    ) -> Decimal | None:
        if account is None or account.available_cash is None or managed is None:
            return None
        if account.scope_completeness is not AccountScopeCompleteness.COMPLETE:
            return None
        return max(account.available_cash - managed * cash_weight, Decimal("0"))

    @staticmethod
    def _lot_quantity(amount: Decimal, price: Decimal, lot_size: int) -> int:
        lots = (amount / price / lot_size).to_integral_value(rounding=ROUND_FLOOR)
        return int(lots) * lot_size

    @staticmethod
    def _relative_gap(draft: _Draft, managed: Decimal | None) -> Decimal:
        if managed in (None, Decimal("0")):
            return Decimal("0")
        amount = draft.amount
        current = draft.current
        price = draft.price
        if amount is None or current is None or price is None:
            return Decimal("0")
        return (amount - Decimal(int(current)) * price) / managed

    def _affordable(
        self,
        desired: int,
        lot_size: int,
        price: Decimal,
        cash: Decimal,
        asset_type: AssetType,
        on_date: date,
    ) -> int:
        low, high = 0, desired // lot_size
        while low < high:
            middle = (low + high + 1) // 2
            quantity = middle * lot_size
            fees = self._fees.calculate(
                asset_type, OrderSide.BUY, Decimal(quantity) * price, on_date
            ).total
            if Decimal(quantity) * price + fees <= cash:
                low = middle
            else:
                high = middle - 1
        return low * lot_size
