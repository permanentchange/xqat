from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from tests.unit.portfolio.test_validation import target
from xqatexp.daily.advice import DailyAdviceService
from xqatexp.domain.contracts import AccountPosition, AccountSnapshot
from xqatexp.domain.enums import (
    AccountScope,
    AccountScopeCompleteness,
    PositionCompleteness,
    TradeAction,
)
from xqatexp.portfolio.rebalance import LotRule


def _account(completeness=PositionCompleteness.COMPLETE):
    return AccountSnapshot(
        "1.0",
        datetime.fromisoformat("2026-09-04T18:00:00+08:00"),
        "CNY",
        AccountScope.STRATEGY_MANAGED,
        AccountScopeCompleteness.COMPLETE,
        completeness,
        Decimal("100000"),
        Decimal("100000"),
        Decimal("0"),
        "fixture",
        (),
    )


def _run(account):
    return DailyAdviceService().run(
        target=target(),
        account=account,
        reference_prices={"600000.SH": Decimal("10")},
        lot_rules={"600000.SH": LotRule(100, 100)},
        run_started_at=datetime.fromisoformat("2026-09-04T19:00:00+08:00"),
    )


def test_no_account_preserves_target_but_never_invents_quantities() -> None:
    advice = _run(None)
    item = advice.items[0]
    assert item.target_weight == Decimal("0.04")
    assert item.action is TradeAction.UNRESOLVED
    assert item.current_quantity is None
    assert item.suggested_quantity is None
    assert "ACCOUNT_NOT_PROVIDED" in item.limitations


def test_confirmed_empty_account_uses_only_cash_above_target_reserve() -> None:
    advice = _run(_account())
    item = advice.items[0]
    assert item.action is TradeAction.BUY
    assert item.theoretical_target_quantity == 400
    assert item.suggested_quantity == 300
    assert item.unresolved_quantity == 100
    assert advice.non_order_disclaimer == "仅供研究参考; 不是订单或投资承诺。"


def test_unknown_holdings_do_not_trigger_assumed_zero_buy() -> None:
    advice = _run(_account(PositionCompleteness.UNKNOWN))
    item = advice.items[0]
    assert item.action is TradeAction.UNRESOLVED
    assert item.current_quantity is None
    assert item.suggested_quantity is None
    assert "POSITIONS_UNKNOWN" in item.limitations


def test_partial_unlisted_holding_is_not_assumed_zero() -> None:
    advice = _run(_account(PositionCompleteness.PARTIAL))
    assert advice.items[0].action is TradeAction.UNRESOLVED
    assert advice.items[0].suggested_quantity is None
    assert "POSITIONS_PARTIAL" in advice.items[0].limitations


def test_confirmed_sell_never_exceeds_sellable_quantity() -> None:
    account = replace(
        _account(),
        positions=(
            AccountPosition(
                "600000.SH",
                500,
                100,
                Decimal("5000"),
                Decimal("10"),
                target().decision_date,
            ),
        ),
    )
    item = _run(account).items[0]
    assert item.action is TradeAction.DECREASE
    assert item.suggested_quantity == 100
    assert item.max_confirmed_sell_quantity == 100


def test_conflicting_complete_account_value_is_explicitly_limited() -> None:
    account = replace(
        _account(),
        available_cash=Decimal("50000"),
        positions=(
            AccountPosition(
                "600001.SH", 100, 100, Decimal("1000"), Decimal("10"), target().decision_date
            ),
        ),
    )
    advice = _run(account)
    assert "ACCOUNT_VALUE_CONFLICT" in advice.limitations
    assert {issue.code for issue in advice.issues} >= {"ADVICE_ACCOUNT_VALUE_CONFLICT"}
