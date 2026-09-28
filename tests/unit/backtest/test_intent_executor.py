from datetime import date
from decimal import Decimal

from xqatexp.backtest.account import SimulatedAccount
from xqatexp.backtest.intent_executor import IntentExecutor
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.intents import (
    CurrentPositionFraction,
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
