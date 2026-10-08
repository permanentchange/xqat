from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STRATEGY_ROOT = ROOT / "src" / "xqatexp" / "strategy"
STRATEGY_IMPL_ROOT = STRATEGY_ROOT / "strategies"
STRATEGY_TEST_ROOT = ROOT / "tests" / "unit" / "strategy" / "strategies"


def test_strategy_root_contains_framework_not_builtin_implementations() -> None:
    forbidden = {
        "declaration.py",
        "drawdown.py",
        "market_regime.py",
        "scoring.py",
        "weekly_parameters.py",
        "weekly_strategy.py",
        "staged_drawdown_declaration.py",
        "staged_drawdown_parameters.py",
        "staged_drawdown_strategy.py",
    }
    assert not {path.name for path in STRATEGY_ROOT.iterdir()} & forbidden


def test_builtin_strategies_have_isolated_source_and_unit_test_packages() -> None:
    expected = {
        "weekly_market_guard_rank_v1": {
            "source": {
                "__init__.py",
                "README.md",
                "parameters.py",
                "declaration.py",
                "strategy.py",
                "scoring.py",
                "market_regime.py",
                "drawdown.py",
            },
            "tests": {
                "test_declaration.py",
                "test_drawdown.py",
                "test_holdings.py",
                "test_market_regime.py",
                "test_scoring.py",
            },
        },
        "staged_drawdown_v1": {
            "source": {
                "__init__.py",
                "README.md",
                "parameters.py",
                "declaration.py",
                "strategy.py",
            },
            "tests": {"test_staged_drawdown.py", "test_tiered_take_profit.py"},
        },
    }
    for strategy_id, layout in expected.items():
        source_dir = STRATEGY_IMPL_ROOT / strategy_id
        test_dir = STRATEGY_TEST_ROOT / strategy_id
        assert source_dir.is_dir()
        assert test_dir.is_dir()
        assert {path.name for path in source_dir.iterdir() if path.is_file()} == layout["source"]
        assert {path.name for path in test_dir.iterdir() if path.is_file()} == layout["tests"]
