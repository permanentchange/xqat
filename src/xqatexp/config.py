from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from xqatexp.domain.contracts import AnalysisPeriod, CustomFactorInput, ResolvedRunContext
from xqatexp.domain.enums import RunMode
from xqatexp.security import validate_disjoint_paths
from xqatexp.strategy.registry import resolve_strategy_spec


class ConfigError(ValueError):
    """A run configuration failed schema or cross-field validation."""


_SECRET_KEY = re.compile(r"token|secret|password|credential|authorization", re.IGNORECASE)
_TOP_LEVEL = {
    "schema_version",
    "mode",
    "strategy_id",
    "strategy_version",
    "research_artifact",
    "start_date",
    "end_date",
    "decision_date",
    "output",
    "account_snapshot",
    "previous_target",
    "strategy_state",
    "custom_factors",
    "strategy",
    "execution",
    "analysis_periods",
}

_EXECUTION_DEFAULTS: dict[str, object] = {
    "initial_cash": Decimal("1000000"),
    "price_model": "NEXT_OPEN",
    "slippage_bps": Decimal("10"),
    "max_volume_participation": Decimal("0.10"),
    "fee_schedule_id": "cn_cash_market_default_v1",
    "dividend_tax_model": "PROVIDER_AFTER_TAX",
    "dividend_tax_rate": Decimal("0"),
}


def _check_secret_keys(value: object, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SECRET_KEY.search(str(key)):
                raise ConfigError(f"CONFIG_SCHEMA_INVALID: secret field at {path}.{key}")
            _check_secret_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_secret_keys(child, f"{path}[{index}]")


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ConfigError(f"CONFIG_VALUE_INVALID: {field} must be numeric")
    try:
        return Decimal(str(value))
    except Exception as error:
        raise ConfigError(f"CONFIG_VALUE_INVALID: {field} must be numeric") from error


def _date(value: object | None, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ConfigError(f"CONFIG_VALUE_INVALID: {field} must be YYYY-MM-DD") from error


def _manifest_sha(path: Path) -> str:
    manifest = path / "manifest.json"
    if not manifest.is_file():
        raise ConfigError(f"DATA_REQUIRED_MISSING: research manifest not found at {path}")
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def resolve_config(
    cli_values: Mapping[str, object],
    toml_path: Path,
    *,
    run_id: str,
    generated_at: datetime,
) -> ResolvedRunContext:
    try:
        raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"CONFIG_SCHEMA_INVALID: cannot read TOML: {error}") from error
    _check_secret_keys(raw)
    unknown = set(raw) - _TOP_LEVEL
    if unknown:
        raise ConfigError(f"CONFIG_SCHEMA_INVALID: unknown top-level field {min(unknown)}")
    if raw.get("schema_version") != "1.0":
        raise ConfigError("CONFIG_SCHEMA_INVALID: schema_version must be 1.0")
    try:
        mode = RunMode(str(cli_values.get("mode") or raw["mode"]))
    except (KeyError, ValueError) as error:
        raise ConfigError("CONFIG_VALUE_INVALID: mode is invalid") from error
    strategy_id = str(raw.get("strategy_id", "weekly_market_guard_rank_v1"))
    strategy_version = str(raw.get("strategy_version", "1.0.0"))
    try:
        strategy_spec = resolve_strategy_spec(strategy_id, strategy_version)
    except ValueError as error:
        raise ConfigError(f"CONFIG_VALUE_INVALID: {error}") from error
    strategy_raw = raw.get("strategy", {})
    if not isinstance(strategy_raw, Mapping):
        raise ConfigError("CONFIG_SCHEMA_INVALID: strategy must be a table")
    try:
        parameters = strategy_spec.normalize_parameters(strategy_raw, cli_values)
    except ValueError as error:
        raise ConfigError(str(error)) from error
    execution_raw = raw.get("execution", {})
    if not isinstance(execution_raw, Mapping):
        raise ConfigError("CONFIG_SCHEMA_INVALID: execution must be a table")
    unknown_execution = set(execution_raw) - set(_EXECUTION_DEFAULTS)
    if unknown_execution:
        raise ConfigError(
            f"CONFIG_SCHEMA_INVALID: unknown execution field {min(unknown_execution)}"
        )
    execution = dict(_EXECUTION_DEFAULTS)
    execution.update(execution_raw)
    execution["slippage_bps"] = _decimal(execution["slippage_bps"], "slippage_bps")
    execution["initial_cash"] = _decimal(execution["initial_cash"], "initial_cash")
    execution["max_volume_participation"] = _decimal(
        execution["max_volume_participation"], "max_volume_participation"
    )
    execution["dividend_tax_rate"] = _decimal(execution["dividend_tax_rate"], "dividend_tax_rate")
    if str(execution["price_model"]) != "NEXT_OPEN":
        raise ConfigError("CONFIG_VALUE_INVALID: unsupported price_model")
    if str(execution["fee_schedule_id"]) != "cn_cash_market_default_v1":
        raise ConfigError("CONFIG_VALUE_INVALID: unsupported fee_schedule_id")
    if cast(Decimal, execution["initial_cash"]) <= 0:
        raise ConfigError("CONFIG_VALUE_INVALID: initial_cash must be positive")
    if not Decimal("0") < cast(Decimal, execution["max_volume_participation"]) <= 1:
        raise ConfigError("CONFIG_VALUE_INVALID: max_volume_participation must be in (0,1]")
    tax_model = str(execution["dividend_tax_model"])
    if tax_model not in {"PROVIDER_AFTER_TAX", "FLAT_RATE"}:
        raise ConfigError("CONFIG_VALUE_INVALID: dividend_tax_model is invalid")
    if tax_model == "FLAT_RATE" and "dividend_tax_rate" not in execution_raw:
        raise ConfigError("CONFIG_VALUE_INVALID: dividend_tax_rate is required for FLAT_RATE")
    if tax_model != "FLAT_RATE" and "dividend_tax_rate" in execution_raw:
        raise ConfigError("CONFIG_VALUE_INVALID: dividend_tax_rate is only valid for FLAT_RATE")
    if not Decimal("0") <= cast(Decimal, execution["dividend_tax_rate"]) <= 1:
        raise ConfigError("CONFIG_VALUE_INVALID: dividend_tax_rate must be in [0,1]")
    research = Path(str(cli_values.get("research_artifact") or raw["research_artifact"])).resolve()
    output_value = cli_values.get("output") or raw.get("output")
    if not output_value:
        raise ConfigError("CONFIG_OUTPUT_REQUIRED: output is required")
    output = Path(str(output_value)).resolve()
    start = _date(cli_values.get("start_date") or raw.get("start_date"), "start_date")
    end = _date(cli_values.get("end_date") or raw.get("end_date"), "end_date")
    decision = _date(cli_values.get("decision_date") or raw.get("decision_date"), "decision_date")
    if mode is RunMode.BACKTEST and (start is None or end is None or start > end):
        raise ConfigError("CONFIG_VALUE_INVALID: backtest requires start_date <= end_date")
    if mode is not RunMode.BACKTEST and decision is None:
        raise ConfigError("CONFIG_VALUE_INVALID: daily mode requires decision_date")
    custom_inputs = []
    for item in raw.get("custom_factors", []):
        factor_path = Path(str(item)).resolve()
        custom_inputs.append(
            CustomFactorInput(factor_path, hashlib.sha256(factor_path.read_bytes()).hexdigest())
        )
    account_path = (
        Path(str(raw["account_snapshot"])).resolve() if raw.get("account_snapshot") else None
    )
    previous_target_path = (
        Path(str(raw["previous_target"])).resolve() if raw.get("previous_target") else None
    )
    strategy_state_value = cli_values.get("strategy_state") or raw.get("strategy_state")
    strategy_state_path = (
        Path(str(strategy_state_value)).resolve() if strategy_state_value else None
    )
    path_inputs = {"research_artifact": research, "config": toml_path.resolve()}
    path_inputs.update(
        {f"custom_factor[{index}]": item.path for index, item in enumerate(custom_inputs)}
    )
    if account_path is not None:
        path_inputs["account_snapshot"] = account_path
    if previous_target_path is not None:
        path_inputs["previous_target"] = previous_target_path
    if strategy_state_path is not None:
        path_inputs["strategy_state"] = strategy_state_path
    validate_disjoint_paths(path_inputs, {"output": output})
    periods_raw = raw.get("analysis_periods", [])
    if not isinstance(periods_raw, list):
        raise ConfigError("CONFIG_SCHEMA_INVALID: analysis_periods must be an array")
    analysis_periods: list[AnalysisPeriod] = []
    labels: set[str] = set()
    if mode is RunMode.BACKTEST:
        for index, item in enumerate(periods_raw):
            if not isinstance(item, Mapping) or set(item) != {
                "label",
                "start_date",
                "end_date",
            }:
                raise ConfigError(
                    f"CONFIG_SCHEMA_INVALID: analysis_periods[{index}] fields are invalid"
                )
            label = str(item["label"]).strip()
            period_start = _date(item["start_date"], f"analysis_periods[{index}].start_date")
            period_end = _date(item["end_date"], f"analysis_periods[{index}].end_date")
            if not label or label in labels:
                raise ConfigError("CONFIG_VALUE_INVALID: analysis_periods labels must be unique")
            if label[0] in "=+-@":
                raise ConfigError("CONFIG_VALUE_INVALID: analysis_periods labels must be CSV-safe")
            if period_start is None or period_end is None or period_start > period_end:
                raise ConfigError(
                    "CONFIG_VALUE_INVALID: analysis_periods requires start_date <= end_date"
                )
            if start is None or end is None or period_start < start or period_end > end:
                raise ConfigError(
                    "CONFIG_VALUE_INVALID: analysis_periods must be within backtest range"
                )
            labels.add(label)
            analysis_periods.append(AnalysisPeriod(label, period_start, period_end))
    analysis_periods.sort(key=lambda item: (item.start, item.end, item.label))
    return ResolvedRunContext(
        run_id=run_id,
        mode=mode,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        parameters=parameters,
        research_artifact_path=research,
        research_artifact_sha256=_manifest_sha(research),
        custom_factor_inputs=tuple(custom_inputs),
        decision_date=decision,
        start_date=start,
        end_date=end,
        account_snapshot_path=account_path,
        previous_target_path=previous_target_path,
        output_path=output,
        execution_assumptions=execution,
        generated_at=generated_at,
        analysis_periods=tuple(analysis_periods),
        strategy_state_path=strategy_state_path,
    )
