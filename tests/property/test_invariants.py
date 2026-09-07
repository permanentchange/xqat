from __future__ import annotations

import hashlib
from contextlib import suppress
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
from hypothesis import given, settings
from hypothesis import strategies as st

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublisher, ArtifactPublishError
from xqatexp.backtest.account import SimulatedAccount
from xqatexp.domain.contracts import ExecutionFees, ExecutionRecord, StrategyDeclaration
from xqatexp.domain.enums import FillStatus, MarketRegime, OrderSide, OverwritePolicy
from xqatexp.research.session import ResearchDataSliceImpl
from xqatexp.strategy.scoring import score_cross_section
from xqatexp.strategy.weekly_strategy import allocate_budget

_WEIGHTS = {
    "momentum_60_ex5": Decimal("0.35"),
    "momentum_40": Decimal("0.20"),
    "trend_stability_60": Decimal("0.15"),
    "volume_price_confirm_20": Decimal("0.10"),
    "low_volatility_20": Decimal("0.10"),
    "profitability": Decimal("0.10"),
}


@given(st.sampled_from(tuple(MarketRegime)), st.integers(min_value=0, max_value=30))
@settings(max_examples=60, deadline=None)
def test_allocated_weights_are_nonnegative_and_conserve_one(
    regime: MarketRegime, count: int
) -> None:
    stock, etf, cash = allocate_budget(regime, count, Decimal("0.05"))
    assert stock >= 0 and etf >= 0 and cash >= 0
    assert stock * count + etf + cash == Decimal("1")


@given(
    st.integers(min_value=0, max_value=50),
    st.integers(min_value=0, max_value=50),
)
@settings(max_examples=80, deadline=None)
def test_random_buy_release_sell_sequence_conserves_equity(
    buy_lots: int, requested_sell_lots: int
) -> None:
    quantity = buy_lots * 100
    sell_quantity = min(quantity, requested_sell_lots * 100)
    price = Decimal("10")
    zero_fees = ExecutionFees(Decimal("0"), Decimal("0"), Decimal("0"))
    account = SimulatedAccount(Decimal("1000000"))
    if quantity:
        account.apply_trade(
            ExecutionRecord(
                date(2026, 9, 4),
                "600000.SH",
                OrderSide.BUY,
                quantity,
                quantity,
                price,
                price * quantity,
                zero_fees,
                FillStatus.FILLED,
                reference_price=price,
            ),
            release_date=date(2026, 9, 7),
        )
        account.release_sellable(date(2026, 9, 7))
    if sell_quantity:
        account.apply_trade(
            ExecutionRecord(
                date(2026, 9, 7),
                "600000.SH",
                OrderSide.SELL,
                sell_quantity,
                sell_quantity,
                price,
                price * sell_quantity,
                zero_fees,
                FillStatus.FILLED,
                reference_price=price,
            )
        )
    assert account.equity({"600000.SH": price}) == Decimal("1000000")
    assert account.cash_available >= 0


@given(st.permutations(("600000.SH", "600001.SH", "600002.SH", "600003.SH")))
@settings(max_examples=24, deadline=None)
def test_factor_ranking_is_invariant_under_input_order(order: tuple[str, ...]) -> None:
    fixed_records = {
        security_id: {
            "momentum_60_ex5_v1": float(index),
            "momentum_40_v1": float(index),
            "trend_stability_60_v1": float(index),
            "volume_price_confirm_20_v1": float(index),
            "volatility_20_v1": float(10 - index),
            "roe_annualized_v1": float(index),
        }
        for index, security_id in enumerate(sorted(order), start=1)
    }
    records = {key: fixed_records[key] for key in order}
    canonical = {key: fixed_records[key] for key in sorted(fixed_records)}
    assert score_cross_section(records, _WEIGHTS) == score_cross_section(canonical, _WEIGHTS)


@given(st.integers(min_value=1, max_value=365))
@settings(max_examples=25, deadline=None)
def test_research_slice_never_exposes_rows_available_after_decision(offset: int) -> None:
    decision = date(2026, 9, 4)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE market_daily(security_id VARCHAR,trade_date DATE,"
            "close_raw DECIMAL(20,10),available_from DATE)"
        )
        connection.execute(
            "INSERT INTO market_daily VALUES ('600000.SH',?,?,?),('600000.SH',?,?,?)",
            [
                decision,
                Decimal("10"),
                decision,
                decision,
                Decimal("99"),
                date.fromordinal(decision.toordinal() + offset),
            ],
        )
        declaration = StrategyDeclaration(
            "property",
            "1",
            "1",
            "WEEKLY",
            1,
            ("close_raw",),
            (),
            (),
            {},
        )
        rows = ResearchDataSliceImpl(connection, declaration, decision, decision).history(
            ("600000.SH",), ("close_raw",), decision, decision
        )
        assert [row["close_raw"] for row in rows] == [Decimal("10.0000000000")]
    finally:
        connection.close()


def _artifact_builder(payload: bytes):
    def build(staging: Path) -> None:
        (staging / "payload.bin").write_bytes(payload)
        manifest = {
            "schema_version": "1.0",
            "artifact_type": "RAW_DATA",
            "artifact_id": "01991a6a-4c00-7000-8000-000000000001",
            "created_at": "2026-09-05T00:00:00Z",
            "producer": {"name": "xqatexp", "version": "0.1.0"},
            "run": None,
            "inputs": [],
            "date_scope": {"start": None, "end": None},
            "files": [
                {
                    "path": "payload.bin",
                    "media_type": "application/octet-stream",
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "row_count": None,
                    "schema_id": None,
                    "schema_version": None,
                }
            ],
            "issues": [],
            "limitations": [],
        }
        (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

    return build


@given(st.binary(min_size=1, max_size=128), st.binary(min_size=1, max_size=128))
@settings(max_examples=30, deadline=None)
def test_failed_overwrite_never_mutates_last_verified_artifact(
    original: bytes, attempted: bytes
) -> None:
    with TemporaryDirectory() as temporary:
        target = Path(temporary) / "result"
        publisher = ArtifactPublisher()
        publisher.publish(_artifact_builder(original), target, OverwritePolicy.ERROR)
        before = {
            item.relative_to(target).as_posix(): item.read_bytes()
            for item in target.rglob("*")
            if item.is_file()
        }

        def interrupted(staging: Path) -> None:
            (staging / "payload.bin").write_bytes(attempted)
            raise RuntimeError("simulated interruption")

        with suppress(ArtifactPublishError):
            publisher.publish(interrupted, target, OverwritePolicy.OVERWRITE)
        after = {
            item.relative_to(target).as_posix(): item.read_bytes()
            for item in target.rglob("*")
            if item.is_file()
        }
        assert after == before
