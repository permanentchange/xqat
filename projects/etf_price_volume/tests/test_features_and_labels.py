from __future__ import annotations

from datetime import timedelta

import numpy as np
import pyarrow as pa
import pytest

from projects.etf_price_volume.research.data import audit_rows
from projects.etf_price_volume.research.features import array, build_features, rolling_groups
from projects.etf_price_volume.research.labels import build_labels

from .conftest import synthetic_bars


def test_all_fourteen_features_and_atr_on_known_linear_bars():
    bars = synthetic_bars(100).to_pylist()
    for i, row in enumerate(bars):
        row.update(
            research_close=10.0 + i,
            research_high=11.0 + i,
            research_low=9.0 + i,
            volume_shares=100 + i,
        )
    f, reasons = build_features(pa.Table.from_pylist(bars))
    row = f.to_pylist()[60]
    expected = {
        "P1": 70 / 65 - 1,
        "P2": 70 / 50 - 1,
        "P3": 70 / 10 - 1,
        "P4": 70 / 40.5 - 1,
        "P5": 60 / 61,
        "P6": 0.0,
        "V1": 160 / 157,
        "V2": 160 / 149.5,
        "V4": 1.0,
        "V5": 158 / 145.5,
        "R3": 2 / 70,
    }
    log_v = np.log(np.arange(100, 160))
    expected["V3"] = (np.log(160) - log_v.mean()) / log_v.std(ddof=1)
    short_returns = 1 / np.arange(50, 70)
    long_returns = 1 / np.arange(10, 70)
    expected["R1"] = short_returns.std(ddof=1)
    expected["R2"] = short_returns.std(ddof=1) / long_returns.std(ddof=1)
    for name, value in expected.items():
        assert row[name] == pytest.approx(value)
    assert any(r["reason"] == "WARMUP_INSUFFICIENT" for r in reasons)


def test_future_changes_cannot_change_past_features_or_groups():
    bars = synthetic_bars(800)
    original, _ = build_features(bars)
    rows = bars.to_pylist()
    for r in rows[600:]:
        for key in ("research_open", "research_high", "research_low", "research_close"):
            r[key] *= 100
        r["volume_shares"] *= 1000
    changed, _ = build_features(pa.Table.from_pylist(rows))
    assert original.slice(0, 600).equals(changed.slice(0, 600))
    a, _ = rolling_groups(array(original, "V2"), 252, 5)
    b, _ = rolling_groups(array(changed, "V2"), 252, 5)
    np.testing.assert_equal(a[:600], b[:600])


def test_relative_volume_excludes_today_and_zero_volume_is_missing():
    rows = synthetic_bars(70).to_pylist()
    for r in rows:
        r["volume_shares"] = 100
    rows[60]["volume_shares"] = 10000
    rows[61]["volume_shares"] = 0
    f, _ = build_features(pa.Table.from_pylist(rows))
    assert f["V2"][60].as_py() == 100
    assert f["V3"][60].as_py() is None  # constant log-history denominator
    assert f["V1"][61].as_py() is None
    assert f["V2"][62].as_py() is None


def test_constant_price_and_volume_do_not_manufacture_groups_or_zero_volatility_ratio():
    rows = synthetic_bars(100).to_pylist()
    for r in rows:
        r.update(research_close=10.0, research_high=10.0, research_low=10.0, volume_shares=100)
    f, _ = build_features(pa.Table.from_pylist(rows))
    assert f["P5"][80].as_py() is None
    assert f["R2"][80].as_py() is None
    groups, reasons = rolling_groups(np.ones(80), 60, 5)
    assert np.isnan(groups).all()
    assert reasons[60] == "DEGENERATE_QUANTILES"


def test_quantile_boundaries_use_only_prior_valid_observations():
    values = np.r_[np.arange(60.0), 1_000_000.0]
    groups, _ = rolling_groups(values, 60, 5)
    assert groups[-1] == 5
    values[-1] = -1_000_000
    other, _ = rolling_groups(values, 60, 5)
    assert other[-1] == 1
    assert np.isnan(groups[:60]).all()


def test_open_to_open_holding_period_and_adverse_excursion():
    rows = synthetic_bars(10).to_pylist()
    for i, r in enumerate(rows):
        r.update(research_open=10.0 + i, research_low=9.0 + i)
    bars = pa.Table.from_pylist(rows)
    boundary = rows[6]["trade_date"]
    labels, excluded = build_labels(bars, [1, 5], boundary)
    assert labels["Y1"][0].as_py() == pytest.approx(12 / 11 - 1)
    assert labels["Y5"][0].as_py() == pytest.approx(16 / 11 - 1)
    assert labels["MAE5"][0].as_py() == pytest.approx(10 / 11 - 1)
    assert labels["entry_date_5"][0].as_py() == rows[1]["trade_date"]
    assert labels["exit_date_5"][0].as_py() == boundary
    assert labels["Y5"][1].as_py() is None
    assert labels["Y1"][5].as_py() is None
    assert excluded


@pytest.mark.parametrize(
    "problem,code",
    [
        ("duplicate", "DUPLICATE_TRADING_DATE"),
        ("missing", "MISSING_OPEN_DAY_BAR"),
        ("ohlc", "INVALID_OHLC_FACTOR_OR_VOLUME"),
        ("delayed", "DAILY_BAR_NOT_VISIBLE_AT_CLOSE"),
    ],
)
def test_basic_data_errors_block_research(problem, code):
    rows = synthetic_bars(10).to_pylist()
    calendar = [{"calendar_date": r["trade_date"], "is_open": True} for r in rows]
    start, end = rows[0]["trade_date"], rows[-1]["trade_date"]
    if problem == "duplicate":
        rows.append(rows[2].copy())
    elif problem == "missing":
        rows.pop(3)
    elif problem == "ohlc":
        rows[2]["high_raw"] = 0
    else:
        rows[2]["available_from"] += timedelta(days=1)
    report = audit_rows(rows, calendar, start, end)
    assert not report["valid"]
    assert code in report["errors"]
