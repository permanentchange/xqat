from datetime import date, timedelta
from decimal import Decimal

from xqatexp.domain.enums import OrderSide, StateRequirement
from xqatexp.strategy.intents import CurrentPositionFraction, InitialCapitalFraction
from xqatexp.strategy.staged_drawdown_declaration import staged_drawdown_declaration
from xqatexp.strategy.staged_drawdown_strategy import StagedDrawdownStrategy
from xqatexp.strategy.state import (
    StrategyPositionState,
    StrategyStateSnapshot,
    StrategyStateView,
)


class _Slice:
    def __init__(self, days, closes):
        self._days = days
        self._closes = closes

    def history(self, security_ids, fields, start, end):
        assert security_ids == ("600000.SH",)
        assert fields == ("research_close", "close_raw")
        return tuple(
            {
                "security_id": "600000.SH",
                "trade_date": day,
                "research_close": close,
                "close_raw": close,
            }
            for day, close in zip(self._days, self._closes, strict=True)
            if start <= day <= end
        )


class _Research:
    def __init__(self, closes):
        self._days = tuple(date(2026, 8, 1) + timedelta(days=index) for index in range(len(closes)))
        self._closes = tuple(Decimal(str(value)) for value in closes)
        self.earliest_date = self._days[0]
        self.decision_date = self._days[-1]

    def trading_days(self, start, end):
        return tuple(day for day in self._days if start <= day <= end)

    def slice(self, as_of_date):
        assert as_of_date == self.decision_date
        return _Slice(self._days, self._closes)

    def next_trading_day(self, after):
        assert after == self.decision_date
        return after + timedelta(days=1)


def _strategy() -> StagedDrawdownStrategy:
    return StagedDrawdownStrategy({"security_id": "600000.SH"})


def _flat_state() -> StrategyStateView:
    return StrategyStateView(
        StrategyStateSnapshot(
            "1.0",
            "staged_drawdown_v1",
            "1.0.0",
            Decimal("100000"),
            None,
            (),
        )
    )


def _position_state(
    *,
    current_basis: str = "10000",
    last_buy: str = "10",
    cumulative: str = "10000",
) -> StrategyStateView:
    return StrategyStateView(
        StrategyStateSnapshot(
            "1.0",
            "staged_drawdown_v1",
            "1.0.0",
            Decimal("100000"),
            date(2026, 8, 19),
            (
                StrategyPositionState(
                    "600000.SH",
                    quantity=1000,
                    remaining_cost_basis=Decimal(current_basis),
                    last_trade_side=OrderSide.BUY,
                    last_trade_date=date(2026, 8, 19),
                    last_trade_quantity=1000,
                    last_trade_price=Decimal(last_buy),
                    last_buy_price=Decimal(last_buy),
                    cumulative_buy_notional=Decimal(cumulative),
                ),
            ),
        )
    )


def test_staged_declaration_is_daily_price_only_and_stateful() -> None:
    declaration = staged_drawdown_declaration({"security_id": "600000.SH"})
    assert declaration.decision_frequency == "DAILY"
    assert declaration.state_requirement is StateRequirement.CONFIRMED_EXECUTION_STATE
    assert [item.dataset for item in declaration.data_requirements] == [
        "TRADING_DAYS",
        "MARKET_HISTORY",
    ]
    assert declaration.required_system_factors == ()


def test_initial_entry_requires_slow_twenty_day_decline() -> None:
    closes = [Decimal("10") - Decimal("0.06") * index for index in range(20)]
    decision = _strategy().generate_stateful_decision(
        _Research(closes), _flat_state(), None, {}
    )
    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.side is OrderSide.BUY
    assert isinstance(intent.sizing, InitialCapitalFraction)
    assert intent.sizing.fraction == Decimal("0.10")
    assert intent.reason_codes == ("INITIAL_ENTRY",)


def test_add_buy_uses_last_actual_buy_price() -> None:
    closes = [Decimal("10") - Decimal("0.06") * index for index in range(20)]
    decision = _strategy().generate_stateful_decision(
        _Research(closes),
        _position_state(last_buy="10", cumulative="10000"),
        None,
        {},
    )
    assert len(decision.intents) == 1
    assert decision.intents[0].side is OrderSide.BUY
    assert decision.intents[0].reason_codes == ("ADD_ON_DECLINE",)


def test_take_profit_uses_remaining_weighted_cost_and_sells_twenty_percent() -> None:
    closes = [Decimal("10") for _ in range(19)] + [Decimal("11.10")]
    decision = _strategy().generate_stateful_decision(
        _Research(closes),
        _position_state(current_basis="10000", last_buy="10"),
        None,
        {},
    )
    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.side is OrderSide.SELL
    assert isinstance(intent.sizing, CurrentPositionFraction)
    assert intent.sizing.fraction == Decimal("0.20")
    assert intent.reason_codes == ("TAKE_PROFIT",)


def test_add_buy_stops_at_initial_capital_limit() -> None:
    closes = [Decimal("10") - Decimal("0.06") * index for index in range(20)]
    decision = _strategy().generate_stateful_decision(
        _Research(closes),
        _position_state(last_buy="10", cumulative="100000"),
        None,
        {},
    )
    assert decision.intents == ()
