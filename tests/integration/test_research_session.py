from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

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
