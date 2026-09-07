from __future__ import annotations

import importlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest


def _config():
    try:
        return importlib.import_module("xqatexp.config")
    except ModuleNotFoundError:
        pytest.fail("xqatexp.config is not implemented", pytrace=False)


def _write_config(
    tmp_path: Path,
    extra_strategy: str = "",
    extra_execution: str = "",
    dividend_tax_model: str = "PROVIDER_AFTER_TAX",
) -> tuple[Path, Path]:
    research = tmp_path / "research"
    research.mkdir()
    (research / "manifest.json").write_text("{}\n", encoding="utf-8")
    config = tmp_path / "run.toml"
    entry_rank = "" if "entry_rank" in extra_strategy else "entry_rank = 20\n"
    exit_rank = "" if "exit_rank" in extra_strategy else "exit_rank = 40\n"
    custom_name = "" if "custom_factor_name" in extra_strategy else 'custom_factor_name = ""\n'
    custom_weight = "" if "custom_factor_weight" in extra_strategy else "custom_factor_weight = 0\n"
    config.write_text(
        f'''schema_version = "1.0"
mode = "BACKTEST"
strategy_id = "weekly_market_guard_rank_v1"
research_artifact = "{research.as_posix()}"
start_date = "2025-01-02"
end_date = "2025-12-31"
output = "{(tmp_path / "toml-output").as_posix()}"

[strategy]
csi300_etf_id = "510300.SH"
{entry_rank}{exit_rank}{custom_name}{custom_weight}{extra_strategy}

[execution]
price_model = "NEXT_OPEN"
slippage_bps = 10
fee_schedule_id = "cn_cash_market_default_v1"
dividend_tax_model = "{dividend_tax_model}"
{extra_execution}
''',
        encoding="utf-8",
    )
    return config, research


def test_cli_business_value_overrides_toml_and_defaults_are_materialized(tmp_path: Path) -> None:
    """Catches wrong precedence or hidden defaults absent from the resolved context."""
    config_path, research = _write_config(tmp_path)
    output = tmp_path / "cli-output"

    context = _config().resolve_config(
        {"output": output, "entry_rank": 25},
        config_path,
        run_id="01991a6a-4c00-7000-8000-000000000002",
        generated_at=datetime(2026, 9, 6, tzinfo=UTC),
    )

    assert context.output_path == output.resolve()
    assert context.research_artifact_path == research.resolve()
    assert context.parameters["entry_rank"] == 25
    assert context.parameters["exit_rank"] == 40
    assert context.parameters["min_holding_weeks"] == 2
    assert context.parameters["max_holding_weeks"] == 8
    assert context.parameters["custom_factor_name"] is None
    assert context.start_date == date(2025, 1, 2)
    assert context.end_date == date(2025, 12, 31)


@pytest.mark.parametrize("relative_output", ("research", "research/nested", "."))
def test_config_rejects_output_that_overlaps_an_input(tmp_path: Path, relative_output: str) -> None:
    config_path, _ = _write_config(tmp_path)
    with pytest.raises(Exception, match="CONFIG_PATH_CONFLICT"):
        _config().resolve_config(
            {"output": tmp_path / relative_output},
            config_path,
            run_id="01991a6a-4c00-7000-8000-000000000002",
            generated_at=datetime(2026, 9, 6, tzinfo=UTC),
        )


def test_backtest_analysis_periods_are_explicit_validated_and_ordered(tmp_path: Path) -> None:
    config_path, _ = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "\n[strategy]",
            """
analysis_periods = [
  { label = "second-half", start_date = "2025-07-01", end_date = "2025-12-31" },
  { label = "first-half", start_date = "2025-01-02", end_date = "2025-06-30" },
]
\n[strategy]""",
        ),
        encoding="utf-8",
    )

    context = _config().resolve_config(
        {},
        config_path,
        run_id="01991a6a-4c00-7000-8000-000000000002",
        generated_at=datetime(2026, 9, 6, tzinfo=UTC),
    )

    assert [(item.label, item.start, item.end) for item in context.analysis_periods] == [
        ("first-half", date(2025, 1, 2), date(2025, 6, 30)),
        ("second-half", date(2025, 7, 1), date(2025, 12, 31)),
    ]

    daily_context = _config().resolve_config(
        {"mode": "DAILY_TARGET", "decision_date": date(2025, 12, 31)},
        config_path,
        run_id="01991a6a-4c00-7000-8000-000000000003",
        generated_at=datetime(2026, 9, 6, tzinfo=UTC),
    )
    assert daily_context.analysis_periods == ()


@pytest.mark.parametrize(
    "periods",
    (
        '[{ label = "bad", start_date = "2024-12-31", end_date = "2025-02-01" }]',
        '[{ label = "same", start_date = "2025-01-02", end_date = "2025-02-01" },'
        ' { label = "same", start_date = "2025-03-01", end_date = "2025-04-01" }]',
        '[{ label = "bad", start_date = "2025-02-01", end_date = "2025-01-02" }]',
        '[{ label = "=FORMULA", start_date = "2025-01-02", end_date = "2025-02-01" }]',
    ),
)
def test_invalid_analysis_periods_fail_during_config_resolution(
    tmp_path: Path, periods: str
) -> None:
    config_path, _ = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "\n[strategy]", f"\nanalysis_periods = {periods}\n\n[strategy]"
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="analysis_periods"):
        _config().resolve_config(
            {},
            config_path,
            run_id="01991a6a-4c00-7000-8000-000000000002",
            generated_at=datetime(2026, 9, 6, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("entry_rank = 45\nexit_rank = 40", "exit_rank"),
        ('custom_factor_name = "quality"\ncustom_factor_weight = 0', "custom_factor_weight"),
        ("single_stock_min_weight = 0.05\nsingle_stock_max_weight = 0.03", "single_stock"),
    ],
)
def test_strategy_cross_field_constraints_fail_before_execution(
    tmp_path: Path, extra: str, message: str
) -> None:
    """Catches internally contradictory strategy settings reaching calculation code."""
    config_path, _ = _write_config(tmp_path, extra)
    with pytest.raises(Exception, match=message):
        _config().resolve_config(
            {},
            config_path,
            run_id="01991a6a-4c00-7000-8000-000000000002",
            generated_at=datetime(2026, 9, 6, tzinfo=UTC),
        )


def test_config_rejects_secret_named_fields(tmp_path: Path) -> None:
    """Catches credentials persisted in ordinary TOML configuration."""
    config_path, _ = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + '\ntushare_token = "forbidden"\n',
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="CONFIG_SCHEMA_INVALID"):
        _config().resolve_config(
            {},
            config_path,
            run_id="01991a6a-4c00-7000-8000-000000000002",
            generated_at=datetime(2026, 9, 6, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("model", "extra_execution"),
    (
        ("UNSUPPORTED", ""),
        ("FLAT_RATE", "dividend_tax_rate = 1.01"),
        ("PROVIDER_AFTER_TAX", "dividend_tax_rate = 0.10"),
    ),
)
def test_dividend_tax_configuration_is_unambiguous(
    tmp_path: Path, model: str, extra_execution: str
) -> None:
    config_path, _ = _write_config(
        tmp_path,
        extra_execution=extra_execution,
        dividend_tax_model=model,
    )
    with pytest.raises(Exception, match="dividend_tax"):
        _config().resolve_config(
            {},
            config_path,
            run_id="01991a6a-4c00-7000-8000-000000000002",
            generated_at=datetime(2026, 9, 6, tzinfo=UTC),
        )
