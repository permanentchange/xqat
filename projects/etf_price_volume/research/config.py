from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROJECT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    source: Path
    raw: dict
    input: Path
    security_id: str
    start: date
    end: date
    holdout: date
    workers: int
    seed: int
    analysis: dict


def load(path: Path) -> Settings:
    raw = tomllib.loads(path.read_text("utf-8"))
    required = {
        "schema_version",
        "input",
        "security_id",
        "development_start",
        "development_end",
        "holdout_start",
        "workers",
        "seed",
        "analysis",
    }
    if set(raw) != required or raw["schema_version"] != "1.0":
        raise ValueError("RESEARCH_CONFIG_INVALID: fields or schema_version")
    start, end, holdout = (
        date.fromisoformat(raw[key])
        for key in ("development_start", "development_end", "holdout_start")
    )
    if not start <= end < holdout:
        raise ValueError("RESEARCH_CONFIG_INVALID: development/holdout dates overlap")
    for key in ("workers", "seed"):
        if type(raw[key]) is not int or raw[key] < (1 if key == "workers" else 0):
            raise ValueError(f"RESEARCH_CONFIG_INVALID: {key}")
    a = raw["analysis"]
    integers = (
        "quantile_window",
        "bootstrap_samples",
        "bootstrap_min_block",
        "bootstrap_horizon_multiplier",
        "minimum_group_observations",
        "minimum_nonoverlap_events",
        "minimum_years",
    )
    fields = {*integers, "horizons", "joint_horizons", "fdr_alpha", "year_direction_fraction"}
    if not isinstance(a, dict) or set(a) != fields:
        raise ValueError("RESEARCH_CONFIG_INVALID: analysis fields")
    for key in integers:
        if type(a[key]) is not int or a[key] < 1:
            raise ValueError(f"RESEARCH_CONFIG_INVALID: {key}")
    if a["bootstrap_samples"] < 100 or a["quantile_window"] < 60:
        raise ValueError("RESEARCH_CONFIG_INVALID: insufficient bootstrap/quantile window")
    for key in ("horizons", "joint_horizons"):
        values = a[key]
        if (
            not isinstance(values, list)
            or not values
            or any(type(v) is not int or v < 1 for v in values)
            or len(values) != len(set(values))
        ):
            raise ValueError(f"RESEARCH_CONFIG_INVALID: {key}")
    if not set(a["joint_horizons"]).issubset(a["horizons"]):
        raise ValueError("RESEARCH_CONFIG_INVALID: joint horizons")
    for key in ("fdr_alpha", "year_direction_fraction"):
        value = a[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not (math.isfinite(value) and 0 < value <= 1)
        ):
            raise ValueError(f"RESEARCH_CONFIG_INVALID: {key}")
    source = Path(raw["input"])
    source = source if source.is_absolute() else ROOT / source
    if not raw["security_id"] or not source.is_dir():
        raise ValueError("RESEARCH_CONFIG_INVALID: security_id/input")
    return Settings(
        path.resolve(),
        raw,
        source.resolve(),
        raw["security_id"],
        start,
        end,
        holdout,
        raw["workers"],
        raw["seed"],
        a,
    )
