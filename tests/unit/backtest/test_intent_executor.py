from datetime import date
from decimal import Decimal

import pytest

from xqatexp.backtest.account import SimulatedAccount
from xqatexp.backtest.intent_executor import IntentExecutor
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.intents import (
    CurrentPositionFraction,
    FixedQuantity,
    InitialCapitalFraction,
    TradeIntent,
    TradeIntentDecision,
)
from xqatexp.strategy.state import StrategyStateReducer, StrategyStateView


class _Data:
    def next_trading_day(self, after: date) -> date:
        return date.fromordinal(after.toordinal() + 1)

    def execution_rows(self, execution_date: date, security_ids):
        del execution_date
        return tuple(
            {
                "security_id": security_id,
                "asset_type": "A_SHARE",
                "open_raw": Decimal("10"),
                "high_raw": Decimal("11"),
                "low_raw": Decimal("9"),
                "valuation_close": Decimal("10"),
                "volume_shares": 1_000_000,
                "up_limit": Decimal("11"),
                "down_limit": Decimal("9"),
                "price_tick": Decimal("0.01"),
                "buy_lot_size": 100,
                "sell_lot_size": 100,
                "is_suspended_full_day": False,
                "is_limit_up_locked": False,
                "is_limit_down_locked": False,
            }
            for security_id in sorted(security_ids)
        )


def _state() -> StrategyStateView:
    reducer = StrategyStateReducer("staged", "1.0.0")
    return StrategyStateView(reducer.initial(Decimal("100000")))


def test_intent_executor_sizes_buy_from_initial_capital_and_sell_from_current_position() -> None:
    account = SimulatedAccount(Decimal("100000"))
    executor = IntentExecutor()
    buy = TradeIntentDecision(
        "decision-buy",
        "staged",
        "1.0.0",
        date(2026, 9, 4),
        date(2026, 9, 7),
        (
            TradeIntent(
                "intent-buy",
                "600000.SH",
                OrderSide.BUY,
                InitialCapitalFraction(Decimal("0.10")),
                ("INITIAL_ENTRY",),
            ),
        ),
    )
    bought = executor.execute(
        data=_Data(),
        account=account,
        decision=buy,
        state=_state(),
        execution_date=date(2026, 9, 7),
        slippage=Decimal("0"),
        participation=Decimal("0.10"),
    )
    assert bought.trades[0][0].filled_quantity == 1000
    assert bought.trades[0][0].decision_id == "decision-buy"
    assert account.positions["600000.SH"] == 1000

    account.release_sellable(date(2026, 9, 8))
    sell = TradeIntentDecision(
        "decision-sell",
        "staged",
        "1.0.0",
        date(2026, 9, 7),
        date(2026, 9, 8),
        (
            TradeIntent(
                "intent-sell",
                "600000.SH",
                OrderSide.SELL,
                CurrentPositionFraction(Decimal("0.20")),
                ("TAKE_PROFIT",),
            ),
        ),
    )
    sold = executor.execute(
        data=_Data(),
        account=account,
        decision=sell,
        state=_state(),
        execution_date=date(2026, 9, 8),
        slippage=Decimal("0"),
        participation=Decimal("0.10"),
    )
    assert sold.trades[0][0].filled_quantity == 200
    assert sold.trades[0][0].side is OrderSide.SELL
    assert account.positions["600000.SH"] == 800


def test_buy_intent_budget_uses_modeled_slippage_price() -> None:
    account = SimulatedAccount(Decimal("100000"))
    decision = TradeIntentDecision(
        "decision-slippage",
        "staged",
        "1.0.0",
        date(2026, 9, 4),
        date(2026, 9, 7),
        (
            TradeIntent(
                "intent-slippage",
                "600000.SH",
                OrderSide.BUY,
                InitialCapitalFraction(Decimal("0.10")),
                ("INITIAL_ENTRY",),
            ),
        ),
    )

    result = IntentExecutor().execute(
        data=_Data(),
        account=account,
        decision=decision,
        state=_state(),
        execution_date=date(2026, 9, 7),
        slippage=Decimal("100"),
        participation=Decimal("0.10"),
    )

    trade = result.trades[0][0]
    assert trade.execution_price == Decimal("10.10")
    assert trade.filled_quantity == 900
    assert trade.gross_amount <= Decimal("10000")


@pytest.mark.parametrize(("requested", "expected"), [(150, 100), (450, 450), (1000, 450)])
def test_fixed_quantity_respects_sell_lot_and_allows_full_odd_lot_exit(requested, expected) -> None:
    account = SimulatedAccount(Decimal("100000"))
    account.positions["600000.SH"] = 450
    account.sellable_quantities["600000.SH"] = 450
    decision = TradeIntentDecision(
        "fixed-sell",
        "staged",
        "1.0.0",
        date(2026, 9, 7),
        date(2026, 9, 8),
        (TradeIntent("fixed", "600000.SH", OrderSide.SELL, FixedQuantity(requested), ("TEST",)),),
    )
    result = IntentExecutor().execute(
        data=_Data(),
        account=account,
        decision=decision,
        state=_state(),
        execution_date=date(2026, 9, 8),
        slippage=Decimal("0"),
        participation=Decimal("0.10"),
    )
    assert result.trades[0][0].filled_quantity == expected
    assert account.positions["600000.SH"] == 450 - expected
