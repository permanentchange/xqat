from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from projects.etf_price_volume.research.config import PROJECT, load
from xqatexp.artifacts.manifest import canonical_json_bytes


def business_days(n: int, start: date = date(2012, 1, 2)) -> list[date]:
    days = []
    while len(days) < n:
        if start.weekday() < 5:
            days.append(start)
        start += timedelta(days=1)
    return days


def synthetic_bars(n: int = 1000) -> pa.Table:
    days = business_days(n)
    rng = np.random.default_rng(72)
    close = 10 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, n)))
    return pa.Table.from_pylist(
        [
            {
                "security_id": "510300.SH",
                "trade_date": d,
                "available_from": d,
                "open_raw": float(c * 0.998),
                "high_raw": float(c * 1.02),
                "low_raw": float(c * 0.98),
                "close_raw": float(c),
                "research_open": float(c * 0.998),
                "research_high": float(c * 1.02),
                "research_low": float(c * 0.98),
                "research_close": float(c),
                "volume_shares": int(rng.lognormal(10, 0.4)),
                "adj_factor": 1.0,
                "amount_cny": float(c * 1000),
                "quality_flags": [],
            }
            for d, c in zip(days, close, strict=True)
        ]
    )


def refresh_manifest(root: Path) -> None:
    import json

    path = root / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    for f in manifest["files"]:
        payload = (root / f["path"]).read_bytes()
        f.update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
    path.write_bytes(canonical_json_bytes(manifest))


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "research"
    tables = root / "tables"
    tables.mkdir(parents=True)
    bars = synthetic_bars()
    dates = bars["trade_date"].to_pylist()
    pq.write_table(bars, tables / "market_daily.parquet")
    pq.write_table(
        pa.Table.from_pylist(
            [{"exchange": "SH", "calendar_date": d, "is_open": True} for d in dates]
        ),
        tables / "trade_calendar.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "security_id": pa.array([], type=pa.string()),
                "ex_date": pa.array([], type=pa.date32()),
            }
        ),
        tables / "corporate_action.parquet",
    )
    manifest = {
        "schema_version": "1.0",
        "artifact_type": "RESEARCH_DATA",
        "artifact_id": "synthetic",
        "created_at": "2026-10-08T00:00:00Z",
        "producer": {"name": "fixture", "version": "1"},
        "run": None,
        "inputs": [],
        "date_scope": {"start": "2013-01-01", "end": str(dates[-1])},
        "issues": [],
        "limitations": [],
        "files": [],
    }
    for member in sorted(tables.glob("*.parquet")):
        payload = member.read_bytes()
        manifest["files"].append(
            {
                "path": str(member.relative_to(root)),
                "media_type": "application/vnd.apache.parquet",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "row_count": pq.read_metadata(member).num_rows,
                "schema_id": "research_tables",
                "schema_version": "1.0",
            }
        )
    (root / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    config = tmp_path / "research.toml"
    text = (PROJECT / "configs/research.toml").read_text("utf-8")
    text = text.replace('"data/research/staged-etf"', f'"{root.as_posix()}"')
    text = text.replace('"2023-12-31"', '"2014-12-31"').replace('"2024-01-01"', '"2015-01-01"')
    text = text.replace("workers = 16", "workers = 1")
    text = text.replace("horizons = [1, 5, 10, 20, 60]", "horizons = [5]")
    text = text.replace("joint_horizons = [5, 20, 60]", "joint_horizons = [5]")
    text = text.replace("bootstrap_samples = 2000", "bootstrap_samples = 100")
    config.write_text(text, "utf-8")
    return load(config)
