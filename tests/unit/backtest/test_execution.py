from datetime import date
from decimal import Decimal

from xqatexp.backtest.execution import ExecutionFacts, ExecutionSimulator
from xqatexp.backtest.fees import FeeModel
from xqatexp.domain.contracts import RebalanceInstruction
from xqatexp.domain.enums import AssetType, FillStatus, OrderSide, UnfilledReason


def instruction(side=OrderSide.BUY, quantity=1000):
    return RebalanceInstruction("600000.SH", side, 0, quantity, quantity, 100, 1, Decimal("10"), ())


def facts(**changes):
    values = dict(
        asset_type=AssetType.A_SHARE,
        open_raw=Decimal("10"),
        high_raw=Decimal("10.20"),
        low_raw=Decimal("9.80"),
        up_limit=Decimal("11"),
        down_limit=Decimal("9"),
        volume_shares=5000,
        price_tick=Decimal("0.01"),
        suspended=False,
        limit_up_locked=False,
        limit_down_locked=False,
    )
    values.update(changes)
    return ExecutionFacts(**values)


def test_slippage_tick_and_volume_capacity_produce_partial_fill() -> None:
    result = ExecutionSimulator(FeeModel.default()).execute(
        instruction(),
        facts(),
        date(2026, 9, 4),
        Decimal("100000"),
        0,
        0,
        Decimal("0.10"),
        Decimal("10"),
    )
    assert result.record is not None
    assert result.record.execution_price == Decimal("10.01")
    assert result.record.filled_quantity == 500
    assert result.record.status is FillStatus.PARTIALLY_FILLED
    assert result.unfilled_reason is UnfilledReason.VOLUME_CAP


def test_suspended_and_locked_limit_are_normal_unfilled_facts() -> None:
    simulator = ExecutionSimulator(FeeModel.default())
    result = simulator.execute(
        instruction(),
        facts(suspended=True),
        date(2026, 9, 4),
        Decimal("100000"),
        0,
        0,
        Decimal("0.1"),
        Decimal("10"),
    )
    assert result.record is None
    assert result.unfilled_reason is UnfilledReason.SUSPENDED
