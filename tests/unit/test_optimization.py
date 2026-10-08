from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from xqatexp.artifacts.publisher import ArtifactPublishError
from xqatexp.optimization.models import Constraint, SearchSpace, StudySpec, TrialResult, digest
from xqatexp.optimization.reporting import write_report
from xqatexp.optimization.storage import finish_backup, prepare_output, study_lock
from xqatexp.strategy.registry import resolve_strategy_spec

ROOT = Path(__file__).resolve().parents[2]


def spec():
    return StudySpec.load(ROOT / "examples/config-staged-etf-opt.toml")


def test_grid_keeps_original_and_produces_504_distinct_normalized_candidates():
    registry = resolve_strategy_spec("staged_drawdown_v1", "1.0.0")
    base = registry.normalize_parameters(
        {"security_id": "510300.SH", "take_profit_mode": "tiered"}, {}
    )
    study = spec()
    values = study.space.generate(
        {**base, **study.fixed_strategy}, lambda p: registry.normalize_parameters(p, {})
    )
    assert len(values) == len({item["id"] for item in values}) == 504
    arrays = {tuple(item["parameters"]["take_profit_levels"]) for item in values}
    assert len(arrays) == 12
    assert tuple(Decimal(str(v)) for v in (0.1, 0.2, 0.3)) in arrays
    assert any(
        item["parameters"]["entry_confirmation_ma_days"] == 5
        and item["parameters"]["entry_confirmation_window_days"] == 10
        and item["parameters"]["take_profit_levels"] == base["take_profit_levels"]
        for item in values
    )
    for item in values:
        assert (
            item["parameters"]["take_profit_sell_fractions"] == base["take_profit_sell_fractions"]
        )
        assert item["parameters"]["buy_fraction"] == base["buy_fraction"]
    shards = [values[i::4] for i in range(4)]
    assert {item["id"] for shard in shards for item in shard} == {item["id"] for item in values}
    assert sum(len(shard) for shard in shards) == 504


def test_numeric_spelling_and_duplicate_candidates_do_not_create_extra_trials():
    assert digest({"x": Decimal("0.10")}) == digest({"x": 0.1})
    space = SearchSpace({"x": [Decimal(".1"), Decimal(".10")]}, {})
    assert len(space.generate({}, dict)) == 1
    with pytest.raises(ValueError, match="empty"):
        SearchSpace({}, {"x": [[2], [1]]}).generate({}, dict)


@pytest.mark.parametrize(
    "count,annual,sharpe,eligible",
    [
        (10, 0.02, 1, False),
        (11, 0.01, 1, False),
        (11, 0.010001, 1, True),
        (11, 0.02, None, False),
        (11, 0.02, float("nan"), False),
        (11, 0.02, float("inf"), False),
        (11, 0.02, True, False),
    ],
)
def test_constraints_are_strict_and_objective_must_be_finite(count, annual, sharpe, eligible):
    metrics = {"trade_count": count, "annualized_return": annual, "sharpe": sharpe}
    study = StudySpec(
        space=SearchSpace({"x": [1]}, {}),
        fixed_strategy={},
        metric="sharpe",
        direction="maximize",
        constraints=(
            Constraint("trade_count", ">", Decimal(10)),
            Constraint("annualized_return", ">", Decimal("0.01")),
        ),
        workers=1,
        start_date=None,
        end_date=None,
    )
    assert (not study.rejection_reasons(metrics)) is eligible


@pytest.mark.parametrize(
    "operator,value,expected",
    [
        (">", 2, True),
        (">=", 1, True),
        ("<", 0, True),
        ("<=", 1, True),
        ("==", 1, True),
    ],
)
def test_generic_constraint_operators(operator, value, expected):
    assert Constraint("x", operator, Decimal(1)).accepts({"x": value}) is expected


def test_ranking_ties_and_minimize_are_deterministic(tmp_path):
    metrics = {"sharpe": 1, "annualized_return": 0.02, "max_drawdown": -0.1}
    a = TrialResult("a", {}, metrics, "succeeded")
    b = replace(a, id="b")
    c = replace(a, id="c", metrics={**metrics, "annualized_return": 0.03})
    d = replace(a, id="d", metrics={**metrics, "max_drawdown": -0.05})
    e = replace(a, id="e", metrics={**metrics, "sharpe": 2})
    study = spec()
    assert [v.id for v in sorted([b, a, c, d, e], key=study.rank_key)] == ["e", "c", "d", "a", "b"]
    assert sorted([a, e], key=replace(study, direction="minimize").rank_key)[0].id == "a"
    assert write_report(tmp_path, study, [b, a, e], 4).id == "e"
    assert "仅报告已完成组合" in (tmp_path / "report.md").read_text()
    assert write_report(tmp_path, study, [], 4) is None
    assert not (tmp_path / "best.json").exists()


@pytest.mark.parametrize(
    "replacement",
    [
        "workers = 0",
        "workers = true",
        'method = "random"',
        'schema_version = "2.0"',
    ],
)
def test_invalid_opt_top_level_values_are_rejected(tmp_path, replacement):
    original = (ROOT / "examples/config-staged-etf-opt.toml").read_text()
    key = replacement.split(" = ")[0]
    lines = [
        replacement if line.startswith(f"{key} = ") else line for line in original.splitlines()
    ]
    path = tmp_path / "opt.toml"
    path.write_text("\n".join(lines))
    with pytest.raises(ValueError, match="OPT_CONFIG_INVALID"):
        StudySpec.load(path)


def test_lock_and_overwrite_keep_backup_until_success(tmp_path):
    output = tmp_path / "study"
    output.mkdir()
    (output / "old.txt").write_text("old")
    with study_lock(output):
        with pytest.raises(ArtifactPublishError, match="LOCKED"), study_lock(output):
            pytest.fail("second writer entered")
        with pytest.raises(ArtifactPublishError, match="EXISTS"):
            prepare_output(output, "error", False)
        backup = prepare_output(output, "overwrite", False)
        assert (backup / "old.txt").read_text() == "old"
        finish_backup(output)
        assert not backup.exists()
    with study_lock(output):
        assert prepare_output(output, "error", True) is None


def test_output_rejects_symlink_and_missing_resume(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ArtifactPublishError, match="symlink"), study_lock(link / "child"):
        pytest.fail("unsafe output accepted")
    with pytest.raises(ValueError, match="does not exist"):
        prepare_output(tmp_path / "missing", "error", True)
