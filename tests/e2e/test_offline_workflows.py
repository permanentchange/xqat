from __future__ import annotations

from pathlib import Path

from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.cli import main
from xqatexp.demo import create_offline_custom_factor, create_offline_research

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_complete_offline_cli_workflow(tmp_path: Path) -> None:
    research = create_offline_research(tmp_path / "research").path
    custom_factor = create_offline_custom_factor(tmp_path / "custom-factor.csv")
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                'schema_version = "1.0"',
                'mode = "BACKTEST"',
                'strategy_id = "weekly_market_guard_rank_v1"',
                f'research_artifact = "{research.as_posix()}"',
                'start_date = "2026-08-31"',
                'end_date = "2026-09-07"',
                'decision_date = "2026-09-04"',
                f'output = "{(tmp_path / "unused").as_posix()}"',
                "",
                "[strategy]",
                'csi300_etf_id = "510300.SH"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    target_result = tmp_path / "daily-target"
    assert (
        main(
            [
                "daily",
                "target",
                "--config",
                str(config),
                "--decision-date",
                "2026-09-04",
                "--output",
                str(target_result),
            ]
        )
        == 0
    )
    custom_config = tmp_path / "custom-config.toml"
    custom_config.write_text(
        config.read_text(encoding="utf-8")
        + 'custom_factor_name = "quality_score"\n'
        + "custom_factor_weight = 0.10\n",
        encoding="utf-8",
    )
    custom_target = tmp_path / "custom-target"
    assert (
        main(
            [
                "daily",
                "target",
                "--config",
                str(custom_config),
                "--decision-date",
                "2026-09-04",
                "--custom-factor",
                str(custom_factor),
                "--output",
                str(custom_target),
            ]
        )
        == 0
    )
    assert ArtifactReader().open(custom_target).manifest["artifact_type"] == "DAILY_TARGET_RESULT"
    assert ArtifactReader().open(target_result).manifest["artifact_type"] == "DAILY_TARGET_RESULT"

    advice_result = tmp_path / "daily-advice"
    assert (
        main(
            [
                "daily",
                "advise",
                "--config",
                str(config),
                "--target",
                str(target_result),
                "--account",
                str(PROJECT_ROOT / "examples" / "account-complete.json"),
                "--output",
                str(advice_result),
            ]
        )
        == 0
    )
    assert ArtifactReader().open(advice_result).manifest["artifact_type"] == "DAILY_ADVICE_RESULT"

    backtest_result = tmp_path / "backtest"
    assert (
        main(
            [
                "backtest",
                "run",
                "--config",
                str(config),
                "--start-date",
                "2026-08-31",
                "--end-date",
                "2026-09-07",
                "--output",
                str(backtest_result),
            ]
        )
        == 0
    )
    assert ArtifactReader().open(backtest_result).manifest["artifact_type"] == "BACKTEST_RESULT"

    factor_report = tmp_path / "factor-check.json"
    assert (
        main(
            [
                "factor",
                "check",
                "--file",
                str(custom_factor),
                "--research",
                str(research),
                "--start",
                "2026-08-31",
                "--end",
                "2026-09-04",
                "--report",
                str(factor_report),
            ]
        )
        == 0
    )
