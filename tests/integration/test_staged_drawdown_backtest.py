from datetime import date, timedelta
from decimal import Decimal

from xqatexp.backtest.engine import BacktestEngine
from xqatexp.domain.enums import OrderSide
from xqatexp.strategy.schedule import DailyCloseSchedule
from xqatexp.strategy.staged_drawdown_strategy import StagedDrawdownStrategy
from xqatexp.strategy.state import StrategyStateReducer


class _Slice:
    def __init__(self, days, closes):
        self._days = days
        self._closes = closes

    def history(self, security_ids, fields, start, end):
        assert security_ids == ("600000.SH",)
        assert fields == ("research_close",)
        return tuple(
            {
                "security_id": "600000.SH",
                "trade_date": day,
                "research_close": self._closes[day],
            }
            for day in self._days
            if start <= day <= end
        )


class _View:
    def __init__(self, data, decision_date):
        self._data = data
        self.decision_date = decision_date
        self.earliest_date = data.days[0]

    def trading_days(self, start, end):
        return tuple(day for day in self._data.days if start <= day <= end)

    def slice(self, as_of_date):
        assert as_of_date == self.decision_date
        return _Slice(self._data.days, self._data.closes)

    def next_trading_day(self, after):
        return self._data.next_trading_day(after)


class _Data:
    def __init__(self):
        start = date(2026, 8, 1)
        self.days = tuple(start + timedelta(days=index) for index in range(24))
        self.closes = {
            day: Decimal("10") - Decimal("0.06") * index
            for index, day in enumerate(self.days[:20])
        }
        self.closes[self.days[20]] = Decimal("7.80")
        self.closes[self.days[21]] = Decimal("11.00")
        self.closes[self.days[22]] = Decimal("11.00")
        self.closes[self.days[23]] = Decimal("11.00")
        self.opens = {
            self.days[19]: Decimal("8.86"),
            self.days[20]: Decimal("8.80"),
            self.days[21]: Decimal("7.80"),
            self.days[22]: Decimal("11.00"),
            self.days[23]: Decimal("11.00"),
        }

    def trading_days(self, start, end):
        return tuple(day for day in self.days if start <= day <= end)

    def next_trading_day(self, after):
        return self.days[self.days.index(after) + 1]

    def view(self, decision_date):
        return _View(self, decision_date)

    def execution_rows(self, execution_date, security_ids):
        open_price = self.opens.get(execution_date, self.closes[execution_date])
        close = self.closes[execution_date]
        return tuple(
            {
                "security_id": security_id,
                "asset_type": "A_SHARE",
                "open_raw": open_price,
                "high_raw": max(open_price, close) + Decimal("1"),
                "low_raw": min(open_price, close) - Decimal("1"),
                "close_raw": close,
                "valuation_close": close,
                "volume_shares": 10_000_000,
                "up_limit": max(open_price, close) + Decimal("2"),
                "down_limit": max(Decimal("0.01"), min(open_price, close) - Decimal("2")),
                "price_tick": Decimal("0.01"),
                "buy_lot_size": 100,
                "sell_lot_size": 100,
                "is_suspended_full_day": False,
                "is_limit_up_locked": False,
                "is_limit_down_locked": False,
            }
            for security_id in sorted(security_ids)
        )


def test_staged_drawdown_backtest_uses_confirmed_fill_state_across_decisions() -> None:
    data = _Data()
    strategy = StagedDrawdownStrategy({"security_id": "600000.SH"})
    result = BacktestEngine().run(
        data=data,
        strategy=strategy,
        parameters={"security_id": "600000.SH"},
        custom=None,
        start_date=data.days[19],
        end_date=data.days[22],
        initial_cash=Decimal("100000"),
        execution_assumptions={
            "slippage_bps": Decimal("0"),
            "max_volume_participation": Decimal("0.10"),
        },
        schedule=DailyCloseSchedule(),
        state_reducer=StrategyStateReducer("staged_drawdown_v1", "1.0.0"),
    )

    assert [(item.side, item.execution_date) for item in result.trades] == [
        (OrderSide.BUY, data.days[20]),
        (OrderSide.BUY, data.days[21]),
        (OrderSide.SELL, data.days[22]),
    ]
    assert [item.filled_quantity for item in result.trades] == [1100, 1200, 400]
    assert result.strategy_state is not None
    position = result.strategy_state.positions[0]
    assert position.quantity == 1900
    assert position.last_trade_side is OrderSide.SELL
    assert position.last_buy_price == Decimal("7.80")
    assert position.cumulative_buy_notional == Decimal("19040.00")
