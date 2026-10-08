from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from xqatexp.domain.contracts import StrategyDeclaration
from xqatexp.research.session import ResearchAccessError, ResearchSession


def _declaration() -> StrategyDeclaration:
    return StrategyDeclaration(
        strategy_id="test",
        strategy_version="1",
        parameter_schema_version="1",
        decision_frequency="WEEKLY",
        lookback_trade_days=1,
        required_fields=("close_raw", "research_close"),
        required_system_factors=("momentum_40_v1",),
        required_custom_factors=(),
        missing_policies={},
    )


def test_session_exposes_only_bounded_declared_historical_facts(tmp_path: Path) -> None:
    from tests.integration.test_research_build import (
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    decision = date(2026, 9, 4)
    with ResearchSession(tmp_path / "research", _declaration()) as session:
        view = session.view(decision)
        snapshot = view.slice(decision)
        assert [item.security_id for item in snapshot.universe()] == ["600000.SH"]
        assert (
            snapshot.history(("600000.SH",), ("close_raw",), decision, decision)[0]["close_raw"]
            is not None
        )
        with pytest.raises(ResearchAccessError, match="STRATEGY_HISTORY_SLICE_VIOLATION"):
            view.slice(decision + timedelta(days=1))
        with pytest.raises(ResearchAccessError, match="undeclared field"):
            snapshot.history(("600000.SH",), ("open_raw",), decision, decision)

    assert session.closed is True


def test_session_rejects_closed_empty_and_out_of_scope_queries(tmp_path: Path) -> None:
    from tests.integration.test_research_build import (
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    decision = date(2026, 9, 4)
    session = ResearchSession(tmp_path / "research", _declaration())
    temporary_directory = session._temporary_directory
    assert temporary_directory.parent == tmp_path.resolve()
    assert temporary_directory.is_dir()
    view = session.view(decision)
    snapshot = view.slice(decision)
    assert session.execution_rows(decision, ()) == ()
    assert snapshot.history((), ("close_raw",), decision, decision) == ()
    assert snapshot.system_factors((), ("600000.SH",)) == ()
    assert snapshot.benchmark_history(("close_raw",), decision, decision) == ()
    with pytest.raises(ResearchAccessError, match="undeclared factor"):
        snapshot.system_factors(("not_declared",), ("600000.SH",))
    with pytest.raises(ResearchAccessError, match="history outside slice"):
        snapshot.history(("600000.SH",), ("close_raw",), decision, decision + timedelta(days=1))
    with pytest.raises(ResearchAccessError, match="next trading day"):
        view.next_trading_day(decision)
    with pytest.raises(ResearchAccessError, match="next trading day"):
        session.next_trading_day(decision)
    with pytest.raises(ResearchAccessError, match="corporate action range"):
        session.corporate_actions(decision, decision - timedelta(days=1))
    with pytest.raises(ResearchAccessError, match="benchmark"):
        session.benchmark_close(decision - timedelta(days=1))
    session.close()
    assert not temporary_directory.exists()
    with pytest.raises(ResearchAccessError, match="session is closed"):
        session.view(decision)
    with pytest.raises(ResearchAccessError, match="session range"):
        session.trading_days(decision, decision)
    with pytest.raises(ResearchAccessError, match="session is closed"):
        session.next_trading_day(decision)
    with pytest.raises(ResearchAccessError, match="session is closed"):
        session.execution_rows(decision, ("600000.SH",))
    with pytest.raises(ResearchAccessError, match="session is closed"):
        session.benchmark_close(decision)


def test_session_preflights_disk_and_cleans_constructor_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xqatexp.research.session as session_module
    from tests.integration.test_research_build import (
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    monkeypatch.setattr(session_module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    with pytest.raises(ResearchAccessError, match="ARTIFACT_RESOURCE_INSUFFICIENT"):
        ResearchSession(tmp_path / "research", _declaration())
    assert not tuple(tmp_path.glob(".xqatexp-tmp-*"))

    monkeypatch.setattr(
        session_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=2 * 1024**3),
    )
    monkeypatch.setattr(
        session_module.duckdb, "connect", lambda _path: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        ResearchSession(tmp_path / "research", _declaration())
    assert not tuple(tmp_path.glob(".xqatexp-tmp-*"))


def test_session_cleans_temp_even_when_connection_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_research_build import (
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    session = ResearchSession(tmp_path / "research", _declaration())
    temporary = session._temporary_directory
    session._connection.close()
    session._connection = SimpleNamespace(  # type: ignore[assignment]
        close=lambda: (_ for _ in ()).throw(RuntimeError("close"))
    )
    with pytest.raises(RuntimeError, match="close"):
        session.close()
    assert session.closed
    assert not temporary.exists()


def test_context_preserves_business_error_when_connection_close_also_fails(
    tmp_path: Path,
) -> None:
    from tests.integration.test_research_build import (
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    session = ResearchSession(tmp_path / "research", _declaration())
    temporary = session._temporary_directory
    session._connection.close()
    session._connection = SimpleNamespace(  # type: ignore[assignment]
        close=lambda: (_ for _ in ()).throw(RuntimeError("close failure"))
    )

    with pytest.raises(ValueError, match="business failure"), session:
        raise ValueError("business failure")
    assert session.closed
    assert not temporary.exists()


def test_security_rules_are_declared_scoped_visible_and_checked_for_readiness(
    tmp_path: Path,
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from tests.integration.test_research_build import (
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )
    from xqatexp.strategy.strategies.staged_drawdown_v1.declaration import (
        staged_drawdown_declaration,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    decision = date(2026, 9, 4)
    declaration = staged_drawdown_declaration(
        {
            "security_id": "600000.SH",
            "take_profit_mode": "tiered",
        }
    )
    requirement = declaration.data_requirements[-1]
    with ResearchSession(tmp_path / "research", declaration) as session:
        view = session.view(decision)
        snapshot = view.slice(decision)
        assert snapshot.security_rules(("600000.SH",), ("sell_lot_size",)) == (
            {"security_id": "600000.SH", "sell_lot_size": 100},
        )
        assert snapshot.security_rules((), ("sell_lot_size",)) == ()
        assert view.requirement_coverage(requirement, None) == 1
        with pytest.raises(ResearchAccessError, match="UNDECLARED"):
            snapshot.security_rules(("600000.SH",), ("buy_lot_size",))
        with pytest.raises(ResearchAccessError, match="security scope"):
            snapshot.security_rules(("600001.SH",), ("sell_lot_size",))
        table = pq.read_table(tmp_path / "research/tables/security_master.parquet")
        index = table.schema.get_field_index("rule_effective_from")
        future = table.set_column(
            index,
            table.schema.field(index),
            pa.array(
                [decision + timedelta(days=1)] * table.num_rows,
                type=pa.date32(),
            ),
        )
        session._connection.register("future_master", future)
        session._connection.execute(
            "CREATE OR REPLACE VIEW security_master AS SELECT * FROM future_master"
        )
        assert snapshot.security_rules(("600000.SH",), ("sell_lot_size",)) == ()
        assert view.requirement_coverage(requirement, None) == 0


def test_entry_confirmation_readiness_and_query_reject_not_yet_available_history(
    tmp_path: Path,
) -> None:
    from decimal import Decimal

    import pyarrow as pa
    import pyarrow.parquet as pq

    from xqatexp.demo import create_offline_research
    from xqatexp.research.readiness import ReadinessChecker
    from xqatexp.strategy.state import StrategyStateReducer, StrategyStateView
    from xqatexp.strategy.strategies.staged_drawdown_v1.strategy import StagedDrawdownStrategy

    root = tmp_path / "research"
    create_offline_research(root)
    days = pq.read_table(root / "tables/trade_calendar.parquet")["calendar_date"].to_pylist()
    strategy = StagedDrawdownStrategy(
        {"security_id": "510300.SH", "entry_confirmation_mode": "ma_rebound"}
    )
    checker = ReadinessChecker()
    with ResearchSession(root, strategy.declaration) as session:
        short_report = checker.check(strategy.declaration, session.view(days[28]))
        assert "STRATEGY_WARMUP_INSUFFICIENT" in short_report.issues
        view = session.view(days[29])
        assert checker.check(strategy.declaration, view).is_ready
        table = pq.read_table(root / "tables/market_daily.parquet")
        rows = table.to_pylist()
        for row in rows:
            if row["security_id"] == "510300.SH" and row["trade_date"] == days[0]:
                row["available_from"] = days[30]
        session._connection.register(
            "unavailable_market", pa.Table.from_pylist(rows, schema=table.schema)
        )
        session._connection.execute(
            "CREATE OR REPLACE VIEW market_daily AS SELECT * FROM unavailable_market"
        )
        assert checker.check(strategy.declaration, view).issues == ("DATA_COVERAGE_INSUFFICIENT",)
        state = StrategyStateReducer("staged_drawdown_v1", "1.0.0").initial(Decimal("100000"))
        with pytest.raises(ValueError, match="DATA_REQUIRED_MISSING"):
            strategy.generate_stateful_decision(view, StrategyStateView(state), None, {})
