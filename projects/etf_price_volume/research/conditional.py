from __future__ import annotations

import numpy as np
import pyarrow as pa
from scipy.stats import spearmanr

from .config import Settings
from .features import FEATURES, PAIRS, array, nullable, rolling_groups


def build_groups(features: pa.Table, window: int) -> tuple[pa.Table, list[dict]]:
    columns = {"date": features["date"]}
    exclusions = []
    for name in FEATURES:
        for bins in (3, 5):
            values, reasons = rolling_groups(array(features, name), window, bins)
            key = f"{name}_q{bins}"
            columns[key] = nullable(values)
            exclusions.extend(
                {"date": features["date"][i].as_py(), "group": key, "reason": reason}
                for i, reason in enumerate(reasons)
                if reason
            )
    return pa.table(columns), exclusions


def nonoverlap(indices: np.ndarray, h: int) -> int:
    last, count = -h - 1, 0
    for i in indices:
        # Touching exit/entry dates are allowed; holding intervals do not overlap.
        if i >= last + h:
            count += 1
            last = int(i)
    return count


def summary(y: np.ndarray, adverse: np.ndarray, mask: np.ndarray, h: int) -> dict:
    mask = mask & np.isfinite(y)
    indices, values = np.flatnonzero(mask), y[mask]
    if not len(values):
        return {
            "n": 0,
            "nonoverlap_n": 0,
            **dict.fromkeys(
                ("mean", "median", "win_probability", "std", "q05", "q95", "mean_mae", "worst_mae")
            ),
        }
    a = adverse[mask]
    return {
        "n": len(values),
        "nonoverlap_n": nonoverlap(indices, h),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "win_probability": float(np.mean(values > 0)),
        "std": float(np.std(values, ddof=1)) if len(values) > 1 else None,
        "q05": float(np.quantile(values, 0.05)),
        "q95": float(np.quantile(values, 0.95)),
        "mean_mae": float(np.mean(a)),
        "worst_mae": float(np.min(a)),
    }


def analyze(features: pa.Table, labels: pa.Table, groups: pa.Table, s: Settings) -> dict:
    dates = features["date"].to_pylist()
    years = np.array([d.year for d in dates])
    development = np.array([s.start <= d <= s.end for d in dates])
    windows = [("development", development)]
    windows += [
        (str(year), development & (years == year)) for year in sorted(set(years[development]))
    ]
    windows += [
        (f"rolling_{year}_{year + 4}", development & (years >= year) & (years <= year + 4))
        for year in range(s.start.year, s.end.year - 3)
    ]
    single, joint, correlations, distributions = [], [], [], []
    for window, period in windows:
        end = max((d for d, used in zip(dates, period, strict=True) if used), default=s.end)
        for name in FEATURES:
            f, g = array(features, name), array(groups, f"{name}_q5")
            observed = f[period & np.isfinite(f)]
            distributions.append(
                {
                    "window": window,
                    "feature": name,
                    "n": len(observed),
                    **{
                        f"q{int(q * 100):02}": float(np.quantile(observed, q))
                        if len(observed)
                        else None
                        for q in (0.01, 0.05, 0.20, 0.50, 0.80, 0.95, 0.99)
                    },
                }
            )
            for h in s.analysis["horizons"]:
                # A window's descriptive labels must end inside that same window.
                ends = labels[f"exit_date_{h}"].to_pylist()
                bounded = period & np.array([d is not None and d <= end for d in ends])
                y, adverse = array(labels, f"Y{h}"), array(labels, f"MAE{h}")
                for q in range(1, 6):
                    single.append(
                        {
                            "window": window,
                            "feature": name,
                            "horizon": h,
                            "group": q,
                            **summary(y, adverse, bounded & (g == q), h),
                        }
                    )
                valid = bounded & np.isfinite(f) & np.isfinite(y)
                rho = (
                    spearmanr(f[valid], y[valid]).statistic
                    if np.sum(valid) > 2 and np.ptp(f[valid]) > 0
                    else np.nan
                )
                correlations.append(
                    {
                        "window": window,
                        "feature": name,
                        "horizon": h,
                        "n": int(np.sum(valid)),
                        "spearman": float(rho) if np.isfinite(rho) else None,
                    }
                )
        for pair, (p, v) in PAIRS.items():
            gp, gv = array(groups, f"{p}_q3"), array(groups, f"{v}_q3")
            for h in s.analysis["joint_horizons"]:
                bounded = period & np.array(
                    [d is not None and d <= end for d in labels[f"exit_date_{h}"].to_pylist()]
                )
                y, adverse = array(labels, f"Y{h}"), array(labels, f"MAE{h}")
                for qp in range(1, 4):
                    for qv in range(1, 4):
                        joint.append(
                            {
                                "window": window,
                                "pair": pair,
                                "price_feature": p,
                                "volume_feature": v,
                                "horizon": h,
                                "price_group": qp,
                                "volume_group": qv,
                                **summary(y, adverse, bounded & (gp == qp) & (gv == qv), h),
                            }
                        )
    corr = []
    for left in FEATURES:
        for right in FEATURES:
            x, y = array(features, left), array(features, right)
            valid = development & np.isfinite(x) & np.isfinite(y)
            rho = (
                spearmanr(x[valid], y[valid]).statistic
                if np.sum(valid) > 2 and np.ptp(x[valid]) > 0 and np.ptp(y[valid]) > 0
                else np.nan
            )
            corr.append(
                {
                    "left": left,
                    "right": right,
                    "n": int(np.sum(valid)),
                    "spearman": float(rho) if np.isfinite(rho) else None,
                    "redundancy_flag": bool(
                        np.isfinite(rho) and abs(rho) >= 0.95 and left != right
                    ),
                }
            )
    return {
        "single": single,
        "joint": joint,
        "correlations": correlations,
        "distributions": distributions,
        "feature_correlations": corr,
    }
