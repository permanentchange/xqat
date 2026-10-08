from __future__ import annotations

import hashlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context

import numpy as np
import pyarrow as pa
from scipy.stats import norm, rankdata
from statsmodels.stats.multitest import multipletests

from .conditional import nonoverlap
from .config import Settings
from .features import FEATURES, PAIRS, array


@dataclass
class TestJob:
    identifier: str
    metadata: dict
    y: np.ndarray
    x: np.ndarray
    valid: np.ndarray
    dates: np.ndarray
    h: int
    seed: int
    analysis: dict


def _design(job: TestJob, indices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x, y = job.x[indices], job.y[indices]
    if not len(indices):
        return x, y
    if job.metadata["kind"] == "spearman":
        rx, ry = rankdata(x[:, 1]), rankdata(y)
        if np.std(rx) == 0 or np.std(ry) == 0:
            return np.empty((0, 2)), np.empty(0)
        x = np.column_stack((np.ones(len(rx)), (rx - np.mean(rx)) / np.std(rx)))
        y = (ry - np.mean(ry)) / np.std(ry)
    return x, y


def _estimate(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    if len(y) <= x.shape[1] + 2:
        return None
    beta, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    return (beta, y - x @ beta) if rank == x.shape[1] else None


def test_job(job: TestJob) -> dict:
    """HAC uses original trading-day positions, including zero scores on excluded dates."""
    indices = np.flatnonzero(job.valid)
    result = {
        "id": job.identifier,
        **job.metadata,
        "horizon": job.h,
        "n": len(indices),
        "nonoverlap_n": nonoverlap(indices, job.h),
        "status": "insufficient",
        "effect": None,
        "se_hac": None,
        "p_hac": None,
        "ci_low": None,
        "ci_high": None,
        "bootstrap_ci_low": None,
        "bootstrap_ci_high": None,
        "bootstrap_valid": 0,
        "year_effects": [],
        "rolling_effects": [],
        "year_direction_fraction": None,
        "year_count": 0,
        "group_counts": [],
        "group_nonoverlap_counts": [],
        "nonoverlap_effect": None,
    }
    x, y = _design(job, indices)
    fitted = _estimate(x, y)
    if fitted is None:
        return result
    beta, residual = fitted
    inv = np.linalg.inv(x.T @ x)
    # A single contrast's score is sufficient for its sandwich variance.
    score = np.zeros(len(job.y))
    score[indices] = (x @ inv[:, 1]) * residual
    lag = min(job.h - 1, len(score) - 1)
    variance = float(score @ score)
    for k in range(1, lag + 1):
        variance += 2 * (1 - k / (lag + 1)) * float(score[k:] @ score[:-k])
    variance *= len(y) / (len(y) - x.shape[1])
    se, effect = float(np.sqrt(max(0, variance))), float(beta[1])
    if se <= 0:
        result["status"] = "zero_variance"
        return result
    result.update(
        status="tested",
        effect=effect,
        se_hac=se,
        p_hac=float(2 * norm.sf(abs(effect / se))),
        ci_low=effect - 1.96 * se,
        ci_high=effect + 1.96 * se,
        hac_lags=lag,
    )
    for group in (0, 1):
        group_indices = indices[job.x[indices, 1] == group]
        result["group_counts"].append(len(group_indices))
        result["group_nonoverlap_counts"].append(nonoverlap(group_indices, job.h))
    if job.metadata["kind"] == "spearman":
        result["group_counts"] = [len(indices)]
        result["group_nonoverlap_counts"] = [nonoverlap(indices, job.h)]
    independent = []
    for i in indices:
        if not independent or i >= independent[-1] + job.h:
            independent.append(int(i))
    fitted_independent = _estimate(*_design(job, np.array(independent, dtype=int)))
    if fitted_independent is not None:
        result["nonoverlap_effect"] = float(fitted_independent[0][1])
    years = np.array([d.year for d in job.dates])
    for year in sorted(set(years[indices])):
        # Exclude labels crossing each year's boundary.
        bounded = np.array(
            [
                i
                for i in indices
                if years[i] == year and i + job.h + 1 < len(years) and years[i + job.h + 1] == year
            ],
            dtype=int,
        )
        fitted_year = _estimate(*_design(job, bounded))
        counts = [int(np.sum(job.x[bounded, 1] == g)) for g in (0, 1)]
        sufficient = min(counts) >= 20 if job.metadata["kind"] != "spearman" else len(bounded) >= 40
        if fitted_year is not None and sufficient:
            result["year_effects"].append(
                {"year": int(year), "n": len(bounded), "effect": float(fitted_year[0][1])}
            )
    result["year_count"] = len(result["year_effects"])
    if result["year_count"]:
        result["year_direction_fraction"] = (
            sum(r["effect"] * effect > 0 for r in result["year_effects"]) / result["year_count"]
        )
    for year in range(int(min(years[indices])), int(max(years[indices])) - 3):
        bounded = np.array(
            [
                i
                for i in indices
                if year <= years[i] <= year + 4
                and i + job.h + 1 < len(years)
                and years[i + job.h + 1] <= year + 4
            ],
            dtype=int,
        )
        fit = _estimate(*_design(job, bounded))
        if fit is not None:
            result["rolling_effects"].append(
                {
                    "start_year": year,
                    "end_year": year + 4,
                    "n": len(bounded),
                    "effect": float(fit[0][1]),
                }
            )
    rng = np.random.default_rng(job.seed)
    first, last = int(indices[0]), int(indices[-1])
    length = last - first + 1
    block = min(
        length,
        max(
            job.analysis["bootstrap_min_block"],
            job.analysis["bootstrap_horizon_multiplier"] * job.h,
        ),
    )
    boot = []
    offsets = np.arange(block)
    for _ in range(job.analysis["bootstrap_samples"]):
        starts = rng.integers(first, last - block + 2, size=int(np.ceil(length / block)))
        sampled = (starts[:, None] + offsets).ravel()[:length]
        sampled = sampled[job.valid[sampled]]
        fit = _estimate(*_design(job, sampled))
        if fit is not None:
            boot.append(float(fit[0][1]))
    result["bootstrap_valid"] = len(boot)
    if len(boot) >= 0.8 * job.analysis["bootstrap_samples"]:
        result["bootstrap_ci_low"], result["bootstrap_ci_high"] = (
            float(v) for v in np.quantile(boot, (0.025, 0.975))
        )
    return result


def jobs(features: pa.Table, labels: pa.Table, groups: pa.Table, s: Settings) -> list[TestJob]:
    dates = np.array(features["date"].to_pylist(), dtype=object)
    development = np.array([s.start <= d <= s.end for d in dates])
    result = []

    def add(metadata: dict, h: int, valid: np.ndarray, x: np.ndarray) -> None:
        identifier = ":".join(str(metadata[k]) for k in sorted(metadata)) + f":H{h}"
        seed = s.seed + int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:4], "big")
        y = array(labels, f"Y{h}")
        valid = development & valid & np.isfinite(y) & np.all(np.isfinite(x), axis=1)
        result.append(TestJob(identifier, metadata, y, x, valid, dates, h, seed, s.analysis))

    for feature in FEATURES:
        g = array(groups, f"{feature}_q5")
        for h in s.analysis["horizons"]:
            add(
                {"kind": "single", "feature": feature, "contrast": "Q5-Q1"},
                h,
                (g == 1) | (g == 5),
                np.column_stack((np.ones(len(g)), (g == 5).astype(float))),
            )
            add(
                {"kind": "single", "feature": feature, "contrast": "extremes-middle"},
                h,
                np.isfinite(g),
                np.column_stack((np.ones(len(g)), ((g == 1) | (g == 5)).astype(float))),
            )
            add(
                {"kind": "spearman", "feature": feature, "contrast": "rank-correlation"},
                h,
                np.isfinite(g),
                np.column_stack((np.ones(len(g)), array(features, feature))),
            )
    for pair, (p, v) in PAIRS.items():
        gp, gv = array(groups, f"{p}_q3"), array(groups, f"{v}_q3")
        for h in s.analysis["joint_horizons"]:
            for q in range(1, 4):
                valid = (gp == q) & ((gv == 1) | (gv == 3))
                base = np.column_stack((np.ones(len(gp)), (gv == 3).astype(float)))
                for kind in ("joint", "controlled_joint"):
                    x = base
                    if kind == "controlled_joint":
                        controls = []
                        for f in dict.fromkeys((p, "R1")):
                            values = array(features, f)
                            observed = values[development & np.isfinite(values)]
                            # Scaling changes conditioning, not the volume contrast coefficient.
                            scale = np.std(observed) if len(observed) else 0
                            controls.append(
                                (values - np.mean(observed)) / scale
                                if scale > 0
                                else np.full(len(values), np.nan)
                            )
                        x = np.column_stack((base, *controls))
                    add(
                        {
                            "kind": kind,
                            "pair": pair,
                            "price_group": q,
                            "price_feature": p,
                            "volume_feature": v,
                            "contrast": "Vhigh-Vlow",
                        },
                        h,
                        valid,
                        x,
                    )
    return result


def select(results: list[dict], analysis: dict) -> list[dict]:
    # Unavailable tests are p=1 and stay in the declared family.
    pvalues = [r["p_hac"] if r["p_hac"] is not None else 1.0 for r in results]
    adjusted = multipletests(pvalues, alpha=analysis["fdr_alpha"], method="fdr_by")[1]
    for r, p in zip(results, adjusted, strict=True):
        reasons = []
        if r["status"] != "tested" or p > analysis["fdr_alpha"]:
            reasons.append("FDR_NOT_PASSED")
        if min(r["group_counts"], default=0) < analysis["minimum_group_observations"]:
            reasons.append("INSUFFICIENT_GROUP_OBSERVATIONS")
        if min(r["group_nonoverlap_counts"], default=0) < analysis["minimum_nonoverlap_events"]:
            reasons.append("INSUFFICIENT_NONOVERLAP_EVENTS")
        if r["year_count"] < analysis["minimum_years"]:
            reasons.append("INSUFFICIENT_YEARS")
        if (r["year_direction_fraction"] or 0) < analysis["year_direction_fraction"]:
            reasons.append("YEAR_DIRECTION_UNSTABLE")
        if (
            r["nonoverlap_effect"] is None
            or r["effect"] is None
            or r["nonoverlap_effect"] * r["effect"] <= 0
        ):
            reasons.append("NONOVERLAP_DIRECTION_UNSUPPORTED")
        lo, hi = r["bootstrap_ci_low"], r["bootstrap_ci_high"]
        if lo is None or hi is None or lo <= 0 <= hi:
            reasons.append("BOOTSTRAP_CI_INCLUDES_ZERO_OR_UNAVAILABLE")
        r.update(
            p_by=float(p),
            rejection_reasons=reasons,
            research_gate_passed=not reasons,
            confirmed_trading_advantage=False,
        )
    controls = {
        (r.get("pair"), r.get("price_group"), r["horizon"]): r
        for r in results
        if r["kind"] == "controlled_joint"
    }
    candidates = []
    for r in results:
        if r["kind"] == "joint":
            control = controls[(r["pair"], r["price_group"], r["horizon"])]
            r["incremental_evidence"] = bool(
                r["research_gate_passed"]
                and control["research_gate_passed"]
                and r["effect"] * control["effect"] > 0
            )
            if r["incremental_evidence"]:
                candidates.append(r)
        elif r["kind"] == "single" and r["research_gate_passed"]:
            candidates.append(r)
    return candidates


def analyze_statistics(
    features: pa.Table, labels: pa.Table, groups: pa.Table, s: Settings, progress=print
) -> tuple[list[dict], list[dict]]:
    tasks = jobs(features, labels, groups, s)
    progress(
        f"STAT_PLAN tests={len(tasks)} workers={s.workers} "
        f"bootstrap={s.analysis['bootstrap_samples']}"
    )
    results = []
    if s.workers == 1:
        for i, task in enumerate(tasks, 1):
            results.append(test_job(task))
            if i % 10 == 0:
                progress(f"STAT_PROGRESS completed={i}/{len(tasks)}")
    else:
        with ProcessPoolExecutor(max_workers=s.workers, mp_context=get_context("spawn")) as pool:
            for i, result in enumerate(pool.map(test_job, tasks, chunksize=1), 1):
                results.append(result)
                if i % 10 == 0:
                    progress(f"STAT_PROGRESS completed={i}/{len(tasks)}")
    return results, select(results, s.analysis)
