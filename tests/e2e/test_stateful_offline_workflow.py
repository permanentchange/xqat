from decimal import Decimal

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.cli import main
from xqatexp.demo import create_offline_research
from xqatexp.strategy.state import StrategyStateReducer
from xqatexp.strategy.state_io import strategy_state_value


def test_stateful_daily_decide_cli_uses_explicit_state(tmp_path) -> None:
    research = create_offline_research(tmp_path / "research").path
    config = tmp_path / "staged.toml"
    config.write_text(
        "\n".join(
            [
                'schema_version = "1.0"',
                'mode = "DAILY_DECISION"',
                'strategy_id = "staged_drawdown_v1"',
                'strategy_version = "1.0.0"',
                f'research_artifact = "{research.as_posix()}"',
                'decision_date = "2026-09-04"',
                f'output = "{(tmp_path / "unused").as_posix()}"',
                "",
                "[strategy]",
                'security_id = "600000.SH"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    state = StrategyStateReducer("staged_drawdown_v1", "1.0.0").initial(
        Decimal("100000")
    )
    state_path.write_bytes(canonical_json_bytes(strategy_state_value(state)))

    output = tmp_path / "decision"
    assert (
        main(
            [
                "daily",
                "decide",
                "--config",
                str(config),
                "--decision-date",
                "2026-09-04",
                "--state",
                str(state_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    opened = ArtifactReader().open(output)
    assert opened.manifest["artifact_type"] == "DAILY_DECISION_RESULT"
    assert {
        "trade_intents.json",
        "strategy_state.json",
        "strategy_diagnostics.json",
    }.issubset(opened.verified_files)
