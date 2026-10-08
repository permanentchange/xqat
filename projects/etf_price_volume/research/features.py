from __future__ import annotations

import numpy as np
import pyarrow as pa

FEATURES = {
    "P1": "close / close[t-5] - 1",
    "P2": "close / close[t-20] - 1",
    "P3": "close / close[t-60] - 1",
    "P4": "close / mean(close[t-59:t]) - 1",
    "P5": "(close - min(low[t-59:t])) / (max(high[t-59:t]) - min(low[t-59:t]))",
    "P6": "close / max(close[t-59:t]) - 1",
    "V1": "volume / mean(volume[t-5:t-1])",
    "V2": "volume / mean(volume[t-20:t-1])",
    "V3": "(log(volume) - mean(log(volume[t-60:t-1]))) / std(ddof=1)",
    "V4": "sum(volume on up-close days, t-19:t) / sum(volume[t-19:t])",
    "V5": "mean(volume[t-4:t]) / mean(volume[t-24:t-5])",
    "R1": "std(close simple returns[t-19:t], ddof=1)",
    "R2": "R1 / std(close simple returns[t-59:t], ddof=1)",
    "R3": "Wilder ATR(14) / close; seed mean of first 14 true ranges",
}
PAIRS = {
    "F1": ("P2", "V2"),
    "F2": ("P1", "V3"),
    "F3": ("P5", "V2"),
    "F4": ("P6", "V3"),
    "F5": ("R1", "V3"),
}


def array(table: pa.Table, name: str) -> np.ndarray:
    return np.array(
        [float(v) if v is not None else np.nan for v in table[name].to_pylist()], dtype=float
    )


def nullable(values: np.ndarray) -> pa.Array:
    return pa.array(values, mask=~np.isfinite(values), type=pa.float64())


def _safe_ratio(a: float, b: float) -> float:
    return a / b if np.isfinite(a) and np.isfinite(b) and b > 0 else np.nan


def build_features(bars: pa.Table) -> tuple[pa.Table, list[dict]]:
    close, high, low, vol = (
        array(bars, k) for k in ("research_close", "research_high", "research_low", "volume_shares")
    )
    n = len(close)
    values = {name: np.full(n, np.nan) for name in FEATURES}
    returns = np.full(n, np.nan)
    returns[1:] = close[1:] / close[:-1] - 1
    # Zero-volume days remain visible, but volume features are missing until a valid window.
    vol = np.where(vol > 0, vol, np.nan)
    tr = np.full(n, np.nan)
    tr[1:] = np.maximum(
        high[1:] - low[1:], np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1]))
    )
    atr = np.full(n, np.nan)
    if n > 14:
        atr[14] = np.mean(tr[1:15])
        for i in range(15, n):
            atr[i] = (13 * atr[i - 1] + tr[i]) / 14
    for i in range(n):
        for key, lag in (("P1", 5), ("P2", 20), ("P3", 60)):
            if i >= lag:
                values[key][i] = _safe_ratio(close[i], close[i - lag]) - 1
        if i >= 59:
            c, hi, lo = close[i - 59 : i + 1], high[i - 59 : i + 1], low[i - 59 : i + 1]
            values["P4"][i] = _safe_ratio(close[i], np.mean(c)) - 1
            values["P5"][i] = _safe_ratio(close[i] - np.min(lo), np.max(hi) - np.min(lo))
            values["P6"][i] = _safe_ratio(close[i], np.max(c)) - 1
        for key, lag in (("V1", 5), ("V2", 20)):
            if i >= lag:
                values[key][i] = _safe_ratio(vol[i], np.mean(vol[i - lag : i]))
        if i >= 60:
            log_history = np.log(vol[i - 60 : i])
            if np.all(np.isfinite(log_history)) and np.ptp(log_history) > 0:
                values["V3"][i] = _safe_ratio(
                    np.log(vol[i]) - np.mean(log_history), np.std(log_history, ddof=1)
                )
        if i >= 20:
            v = vol[i - 19 : i + 1]
            values["V4"][i] = _safe_ratio(np.sum(v * (returns[i - 19 : i + 1] > 0)), np.sum(v))
            values["R1"][i] = np.std(returns[i - 19 : i + 1], ddof=1)
        if i >= 24:
            values["V5"][i] = _safe_ratio(np.mean(vol[i - 4 : i + 1]), np.mean(vol[i - 24 : i - 4]))
        if i >= 60:
            values["R2"][i] = _safe_ratio(values["R1"][i], np.std(returns[i - 59 : i + 1], ddof=1))
        values["R3"][i] = _safe_ratio(atr[i], close[i])
    table = pa.table({"date": bars["trade_date"], **{k: nullable(v) for k, v in values.items()}})
    reasons = []
    warmup = dict(
        zip(FEATURES, (5, 20, 60, 59, 59, 59, 5, 20, 60, 20, 24, 20, 60, 14), strict=True)
    )
    for key, v in values.items():
        for i in np.flatnonzero(~np.isfinite(v)):
            reasons.append(
                {
                    "date": bars["trade_date"][int(i)].as_py(),
                    "feature": key,
                    "reason": "WARMUP_INSUFFICIENT"
                    if i < warmup[key]
                    else "INVALID_WINDOW_OR_ZERO_DENOMINATOR",
                }
            )
    return table, reasons


def rolling_groups(values: np.ndarray, window: int, bins: int) -> tuple[np.ndarray, np.ndarray]:
    """Ranks use exactly the prior window of valid values, excluding today's observation."""
    groups, reasons = np.full(len(values), np.nan), np.full(len(values), "", dtype=object)
    history: list[float] = []
    for i, value in enumerate(values):
        if not np.isfinite(value):
            reasons[i] = "MISSING_FEATURE"
        elif len(history) < window:
            reasons[i] = "QUANTILE_WARMUP_INSUFFICIENT"
        else:
            cuts = np.quantile(history[-window:], np.arange(1, bins) / bins)
            if len(np.unique(cuts)) != bins - 1 or min(history[-window:]) == max(history[-window:]):
                reasons[i] = "DEGENERATE_QUANTILES"
            else:
                groups[i] = 1 + np.searchsorted(cuts, value, side="right")
        if np.isfinite(value):
            history.append(float(value))
    return groups, reasons
