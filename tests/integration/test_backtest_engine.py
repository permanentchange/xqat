from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from tests.unit.portfolio.test_validation import target
from xqatexp.backtest.engine import BacktestEngine
from xqatexp.domain.contracts import TargetPosition
from xqatexp.domain.enums import AssetType, OrderSide


class _View:
    def __init__(self, decision_date: date, next_day: date) -> None:
        self.decision_date = decision_date
        self.earliest_date = date(2025, 1, 1)
        self._next_day = next_day

    def next_trading_day(self, after: date) -> date:
        assert after == self.decision_date
        return self._next_day


class _Data:
    days = (
        date(2026, 9, 4),
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 10),
        date(2026, 9, 11),
        date(2026, 9, 14),
        date(2026, 9, 15),
    )

    def trading_days(self, start: date, end: date):
        return tuple(day for day in self.days if start <= day <= end)

    def next_trading_day(self, after: date):
        return self.days[self.days.index(after) + 1]

    def view(self, decision_date: date):
        return _View(decision_date, self.next_trading_day(decision_date))

    def execution_rows(self, execution_date: date, security_ids):
        del execution_date
        return tuple(
            {
                "security_id": security_id,
                "asset_type": "A_SHARE",
                "open_raw": Decimal("10"),
                "high_raw": Decimal("11"),
                "low_raw": Decimal("9"),
                "close_raw": Decimal("10"),
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


class _Strategy:
    def generate_target(self, research, custom, parameters):
        del custom, parameters
        if research.decision_date == date(2026, 9, 4):
            return replace(target(Decimal("0.50"), Decimal("0.50")))
        assert research.decision_date == date(2026, 9, 11)
        return replace(
            target(Decimal("1"), Decimal("0")),
            decision_date=research.decision_date,
            effective_from=date(2026, 9, 14),
            positions=(
                TargetPosition(
                    "600001.SH", AssetType.A_SHARE, Decimal("1"), 1, 90.0, None, (), 1
                ),
            ),
        )


def test_engine_executes_only_next_day_and_reuses_actual_sell_cash() -> None:
    result = BacktestEngine().run(
        data=_Data(),
        strategy=_Strategy(),
        parameters={},
        custom=None,
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 15),
        initial_cash=Decimal("10000"),
        execution_assumptions={
            "slippage_bps": Decimal("0"),
            "max_volume_participation": Decimal("0.10"),
        },
    )
    assert [(trade.execution_date, trade.security_id, trade.side) for trade in result.trades] == [
        (date(2026, 9, 7), "600000.SH", OrderSide.BUY),
        (date(2026, 9, 14), "600000.SH", OrderSide.SELL),
        (date(2026, 9, 14), "600001.SH", OrderSide.BUY),
    ]
    assert result.trades[2].filled_quantity == 900
    assert result.portfolio_daily[0].valuation_date == date(2026, 9, 4)
    assert result.portfolio_daily[-1].nav == Decimal("9982.31")
    assert all(trade.execution_date > result.targets[0].decision_date for trade in result.trades)
