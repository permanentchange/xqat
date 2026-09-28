from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STRATEGY_ROOT = ROOT / "src" / "xqatexp" / "strategy"


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
    assert (STRATEGY_ROOT / "strategies" / "weekly_market_guard_rank_v1").is_dir()
    assert (STRATEGY_ROOT / "strategies" / "staged_drawdown_v1").is_dir()
