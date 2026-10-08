from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from statsmodels.regression.linear_model import OLS
from statsmodels.stats.sandwich_covariance import cov_hac

from projects.etf_price_volume.research.conditional import build_groups
from projects.etf_price_volume.research.features import build_features
from projects.etf_price_volume.research.labels import build_labels
from projects.etf_price_volume.research.statistics import (
    TestJob as ResearchTestJob,
)
from projects.etf_price_volume.research.statistics import (
    analyze_statistics,
    select,
)
from projects.etf_price_volume.research.statistics import (
    test_job as evaluate_test,
)

from .conftest import business_days, synthetic_bars


def job(inputs, n=1000):
    rng = np.random.default_rng(212)
    group = rng.integers(0, 2, n)
    y = rng.normal(0, 0.1, n) + group * 0.02
    return ResearchTestJob(
        "test",
        {"kind": "single", "contrast": "Q5-Q1"},
        y,
        np.column_stack((np.ones(n), group)),
        np.ones(n, dtype=bool),
        np.array(business_days(n), dtype=object),
        5,
        17,
        inputs.analysis,
    )


def test_hac_matches_statsmodels_on_contiguous_dates(inputs):
    task = job(inputs)
    result = evaluate_test(task)
    reference = cov_hac(OLS(task.y, task.x).fit(), nlags=4, use_correction=True)
    assert result["se_hac"] == pytest.approx(np.sqrt(reference[1, 1]))
    assert result["effect"] == pytest.approx(OLS(task.y, task.x).fit().params[1])


def test_hac_preserves_gaps_in_trading_day_positions(inputs):
    task = job(inputs)
    task.valid[::3] = False
    result = evaluate_test(task)
    valid = np.flatnonzero(task.valid)
    x, y = task.x[valid], task.y[valid]
    fit = OLS(y, x).fit()
    scores = np.zeros((len(task.y), 2))
    scores[valid] = x * fit.resid[:, None]
    sandwich = scores.T @ scores
    for lag in range(1, 5):
        term = scores[lag:].T @ scores[:-lag]
        sandwich += (1 - lag / 5) * (term + term.T)
    inverse = np.linalg.inv(x.T @ x)
    covariance = inverse @ sandwich @ inverse * len(y) / (len(y) - 2)
    assert result["se_hac"] == pytest.approx(np.sqrt(covariance[1, 1]))


def test_price_control_removes_spurious_volume_effect(inputs):
    task = job(inputs, 1500)
    rng = np.random.default_rng(188)
    price = rng.normal(size=1500)
    risk = rng.normal(size=1500)
    volume_group = (price + rng.normal(0, 0.5, 1500) > 0).astype(float)
    task.y = 0.1 * price + 0.01 * rng.normal(size=1500)
    task.x = np.column_stack((np.ones(1500), volume_group))
    raw = evaluate_test(task)
    controlled = evaluate_test(replace(task, x=np.column_stack((task.x, price, risk))))
    assert raw["p_hac"] < 1e-8
    assert controlled["p_hac"] > 0.05


def test_complete_fdr_family_retains_unestimable_and_rejected_tests(inputs):
    task = job(inputs)
    result = evaluate_test(task)
    absent = evaluate_test(replace(task, identifier="empty", valid=np.zeros(1000, dtype=bool)))
    candidates = select([result, absent], inputs.analysis)
    assert absent["p_by"] == 1
    assert "FDR_NOT_PASSED" in absent["rejection_reasons"]
    assert all(not r["confirmed_trading_advantage"] for r in candidates)
    assert len([result, absent]) == 2


def test_serial_parallel_results_and_seeds_match(inputs):
    bars = synthetic_bars()
    f, _ = build_features(bars)
    groups, _ = build_groups(f, 252)
    labels, _ = build_labels(bars, [5], inputs.end)
    one = analyze_statistics(f, labels, groups, inputs, progress=lambda _: None)
    two = analyze_statistics(f, labels, groups, replace(inputs, workers=2), progress=lambda _: None)
    assert one == two
    assert len(one[0]) == 72


def test_constant_returns_and_features_do_not_claim_an_advantage(inputs):
    task = job(inputs)
    task.y[:] = 0
    results = [evaluate_test(task)]
    assert select(results, inputs.analysis) == []
    assert results[0]["status"] == "zero_variance"
