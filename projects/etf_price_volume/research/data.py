from __future__ import annotations

from datetime import date

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from xqatexp.artifacts.readers import ArtifactReader

from .config import Settings


def audit_rows(rows: list[dict], calendar: list[dict], start: date, end: date) -> dict:
    """Audit only warmup/development bars; never repair market observations."""
    errors, warnings = [], []
    dates = [r["trade_date"] for r in rows]
    if len(dates) != len(set(dates)):
        errors.append("DUPLICATE_TRADING_DATE")
    if dates != sorted(dates):
        warnings.append("INPUT_REORDERED_BY_DATE")
    opens = {r["calendar_date"] for r in calendar if r["is_open"]}
    extra = sorted(set(dates) - opens)
    missing = sorted({d for d in opens if start <= d <= end} - set(dates))
    if extra:
        errors.append("BAR_ON_CLOSED_OR_UNKNOWN_DATE")
    if missing:
        errors.append("MISSING_OPEN_DAY_BAR")
    bad, zero, delayed, flagged, jumps, factor_jumps = [], [], [], [], [], []
    ordered = sorted(rows, key=lambda r: r["trade_date"])
    for i, r in enumerate(ordered):
        d = r["trade_date"]
        for suffix in ("raw", "research"):
            keys = (
                [f"{n}_raw" for n in ("open", "high", "low", "close")]
                if suffix == "raw"
                else [f"research_{n}" for n in ("open", "high", "low", "close")]
            )
            values = [float(r[k]) if r.get(k) is not None else float("nan") for k in keys]
            o, h, low, c = values
            if (
                not all(np.isfinite(values))
                or min(values) <= 0
                or not low <= min(o, c) <= max(o, c) <= h
            ):
                bad.append(d)
        factor = r.get("adj_factor")
        volume = r.get("volume_shares")
        if (
            factor is None
            or not np.isfinite(float(factor))
            or factor <= 0
            or volume is None
            or volume < 0
        ):
            bad.append(d)
        if volume == 0:
            zero.append(d)
        if r.get("available_from") is None or r["available_from"] > d:
            delayed.append(d)
        if r.get("quality_flags"):
            flagged.append({"date": d, "flags": r["quality_flags"]})
        if i:
            prev = ordered[i - 1]
            if prev.get("research_close") and r.get("research_close"):
                change = float(r["research_close"] / prev["research_close"] - 1)
                if abs(change) > 0.15:
                    jumps.append({"date": d, "return": change})
            if prev.get("adj_factor") and factor and factor != prev["adj_factor"]:
                factor_jumps.append({"date": d, "ratio": float(factor / prev["adj_factor"])})
    if bad:
        errors.append("INVALID_OHLC_FACTOR_OR_VOLUME")
    if delayed:
        errors.append("DAILY_BAR_NOT_VISIBLE_AT_CLOSE")
    for condition, code in (
        (zero, "ZERO_VOLUME"),
        (flagged, "SOURCE_QUALITY_FLAGS"),
        (jumps, "LARGE_PRICE_JUMPS"),
        (factor_jumps, "FACTOR_CHANGES_REQUIRE_EVENT_REVIEW"),
    ):
        if condition:
            warnings.append(code)
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "row_count": len(rows),
        "missing_dates": missing,
        "extra_dates": extra,
        "invalid_dates": sorted(set(bad)),
        "zero_volume_dates": zero,
        "delayed_dates": delayed,
        "quality_flags": flagged,
        "large_jumps": jumps,
        "factor_changes": factor_jumps,
    }


def load_data(s: Settings) -> tuple[pa.Table, dict]:
    opened = ArtifactReader().open(s.input)
    if opened.manifest["artifact_type"] != "RESEARCH_DATA":
        raise ValueError("RESEARCH_INPUT_INVALID: expected RESEARCH_DATA")
    tables = s.input / "tables"
    # Only dates are inspected outside development; no held-out OHLCV is decoded.
    dates = (
        pq.read_table(
            tables / "market_daily.parquet",
            columns=["trade_date"],
            filters=[("security_id", "=", s.security_id)],
        )
        .column(0)
        .to_pylist()
    )
    if not dates:
        raise ValueError("RESEARCH_INPUT_INVALID: security has no bars")
    first = min(dates)
    rows = pq.read_table(
        tables / "market_daily.parquet",
        filters=[("security_id", "=", s.security_id), ("trade_date", "<=", s.end)],
    ).to_pylist()
    calendar = pq.read_table(
        tables / "trade_calendar.parquet",
        filters=[
            ("exchange", "=", "SH"),
            ("calendar_date", ">=", first),
            ("calendar_date", "<=", s.end),
        ],
    ).to_pylist()
    report = audit_rows(rows, calendar, first, s.end)
    scope = opened.manifest["date_scope"]
    report.update(
        {
            "security_id": s.security_id,
            "declared_scope": scope,
            "observed_scope": {"start": min(dates), "end": max(dates)},
            "development_start": s.start,
            "development_end": s.end,
            "holdout_start": s.holdout,
            "holdout_ohlcv_loaded": False,
            "limitations": [
                "ADJUSTED_PRICE_RETURN_PROXY",
                "HISTORICALLY_EXPOSED_HOLDOUT",
                "ETF_EVENT_COMPLETENESS_UNVERIFIED",
                "VOLUME_SPLIT_BASIS_UNVERIFIED",
            ],
        }
    )
    if date.fromisoformat(scope["start"]) > min(dates) or date.fromisoformat(scope["end"]) < max(
        dates
    ):
        report["warnings"].append("MANIFEST_OBSERVED_SCOPE_MISMATCH")
    actions = pq.read_table(
        tables / "corporate_action.parquet",
        filters=[("security_id", "=", s.security_id), ("ex_date", "<=", s.end)],
    )
    report["development_corporate_action_rows"] = actions.num_rows
    if not actions.num_rows:
        report["warnings"].append("ETF_CORPORATE_ACTIONS_MISSING")
    report["development_rows"] = sum(s.start <= r["trade_date"] <= s.end for r in rows)
    if report["development_rows"] == 0:
        report["errors"].append("EMPTY_DEVELOPMENT_PERIOD")
        report["valid"] = False
    return pa.Table.from_pylist(sorted(rows, key=lambda r: r["trade_date"])), report
