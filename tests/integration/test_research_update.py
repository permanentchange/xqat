from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from xqatexp.domain.enums import OverwritePolicy
from xqatexp.research.tables import ResearchBuildConfig, ResearchBuilder


def _table_hashes(path: Path) -> dict[str, str]:
    return {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted((path / "tables").glob("*.parquet"))
    }


def test_no_change_update_republishes_identical_research_tables(tmp_path: Path) -> None:
    # The builder integration test owns transformation coverage; this freezes incremental reuse.
    from tests.integration.test_research_build import (
        test_research_build_normalizes_units_prices_and_all_table_schemas,
    )

    test_research_build_normalizes_units_prices_and_all_table_schemas(tmp_path)
    base = tmp_path / "research"
    updated = tmp_path / "research-updated"
    config = ResearchBuildConfig(
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 4),
        csi300_etf_id="510300.SH",
        existing_policy=OverwritePolicy.ERROR,
    )
    ResearchBuilder().update(base, (), config, updated)
    assert _table_hashes(updated) == _table_hashes(base)
