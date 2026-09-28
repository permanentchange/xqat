from __future__ import annotations

from datetime import date
from decimal import Decimal

from xqatexp.backtest.account import SimulatedAccount
from xqatexp.backtest.decision_executor import AllocationDecisionExecutor
from xqatexp.domain.contracts import AllocationDecision, TargetPortfolio, TargetPosition
from xqatexp.domain.enums import AssetType, OrderSide


class _Data:
    def next_trading_day(self, after: date) -> date:
        assert after == date(2026, 9, 7)
        return date(2026, 9, 8)

    def execution_rows(self, execution_date: date, security_ids):
        assert execution_date == date(2026, 9, 7)
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


def test_allocation_executor_reuses_real_sell_cash_and_preserves_provenance() -> None:
    account = SimulatedAccount(
        Decimal("0"),
        positions={"600000.SH": 500},
        sellable={"600000.SH": 500},
    )
    target = TargetPortfolio(
        "test",
        "1.0.0",
        date(2026, 9, 4),
        date(2026, 9, 7),
        (
            TargetPosition(
                "600001.SH",
                AssetType.A_SHARE,
                Decimal("1"),
                None,
                ("TARGET_INCREASE",),
                1,
            ),
        ),
        (),
        Decimal("0"),
        (),
    )
    result = AllocationDecisionExecutor().execute(
        data=_Data(),
        account=account,
        decision=AllocationDecision("decision-1", target),
        execution_date=date(2026, 9, 7),
        slippage=Decimal("0"),
        participation=Decimal("0.10"),
    )

    assert [(record.security_id, record.side) for record, _ in result.trades] == [
        ("600000.SH", OrderSide.SELL),
        ("600001.SH", OrderSide.BUY),
    ]
    assert result.trades[1][0].filled_quantity == 400
    assert all(record.decision_id == "decision-1" for record, _ in result.trades)
    assert all(record.instruction_id for record, _ in result.trades)
    assert result.unfilled == ()
