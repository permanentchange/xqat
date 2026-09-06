from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from xqatexp.domain.contracts import CustomFactorInput, ResolvedRunContext
from xqatexp.domain.enums import RunMode


class ConfigError(ValueError):
    pass


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
    "custom_factors",
    "strategy",
    "execution",
}

_STRATEGY_DEFAULTS: dict[str, object] = {
    "entry_rank": 20,
    "exit_rank": 40,
    "min_holding_weeks": 2,
    "max_holding_weeks": 8,
    "min_listing_trade_days": 252,
    "min_size_percentile": Decimal("0.20"),
    "min_amount_percentile": Decimal("0.20"),
    "strong_stock_count": 20,
    "neutral_stock_count": 10,
    "single_stock_min_weight": Decimal("0.03"),
    "single_stock_max_weight": Decimal("0.05"),
    "caution_drawdown": Decimal("-0.08"),
    "defensive_drawdown": Decimal("-0.12"),
    "recovery_drawdown": Decimal("-0.05"),
    "recovery_weeks": 2,
    "custom_factor_name": None,
    "custom_factor_weight": Decimal("0"),
    "custom_factor_direction": "HIGHER_BETTER",
    "custom_factor_missing_policy": "EXACT",
    "score_weights": {
        "momentum_60_ex5": Decimal("0.35"),
        "momentum_40": Decimal("0.20"),
        "trend_stability_60": Decimal("0.15"),
        "volume_price_confirm_20": Decimal("0.10"),
        "low_volatility_20": Decimal("0.10"),
        "profitability": Decimal("0.10"),
    },
}

_EXECUTION_DEFAULTS: dict[str, object] = {
    "initial_cash": Decimal("1000000"),
    "price_model": "NEXT_OPEN",
    "slippage_bps": Decimal("10"),
    "max_volume_participation": Decimal("0.10"),
    "fee_schedule_id": "cn_cash_market_default_v1",
    "dividend_tax_model": "PROVIDER_AFTER_TAX",
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


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"CONFIG_VALUE_INVALID: {field} must be an integer")
    try:
        normalized = int(str(value))
    except (TypeError, ValueError) as error:
        raise ConfigError(f"CONFIG_VALUE_INVALID: {field} must be an integer") from error
    if str(normalized) != str(value):
        raise ConfigError(f"CONFIG_VALUE_INVALID: {field} must be an integer")
    return normalized


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


def _normalize_strategy(raw: Mapping[str, object], cli: Mapping[str, object]) -> dict[str, object]:
    unknown = set(raw) - (set(_STRATEGY_DEFAULTS) | {"csi300_etf_id"})
    if unknown:
        raise ConfigError(f"CONFIG_SCHEMA_INVALID: unknown strategy field {min(unknown)}")
    result = dict(_STRATEGY_DEFAULTS)
    default_weights = cast(Mapping[str, object], _STRATEGY_DEFAULTS["score_weights"])
    result["score_weights"] = dict(default_weights)
    result.update(raw)
    for key in set(result) | {"csi300_etf_id"}:
        if key in cli and cli[key] is not None:
            result[key] = cli[key]
    if result.get("custom_factor_name") == "":
        result["custom_factor_name"] = None
    decimal_fields = {
        "min_size_percentile",
        "min_amount_percentile",
        "single_stock_min_weight",
        "single_stock_max_weight",
        "caution_drawdown",
        "defensive_drawdown",
        "recovery_drawdown",
        "custom_factor_weight",
    }
    for field in decimal_fields:
        result[field] = _decimal(result[field], field)
    integer_fields = {
        "entry_rank",
        "exit_rank",
        "min_holding_weeks",
        "max_holding_weeks",
        "min_listing_trade_days",
        "strong_stock_count",
        "neutral_stock_count",
        "recovery_weeks",
    }
    for field in integer_fields:
        result[field] = _integer(result[field], field)
    weights = result["score_weights"]
    if not isinstance(weights, Mapping) or set(weights) != set(default_weights):
        raise ConfigError("CONFIG_VALUE_INVALID: score_weights must contain exactly six keys")
    normalized_weights = {
        key: _decimal(value, f"score_weights.{key}") for key, value in weights.items()
    }
    if any(weight < 0 for weight in normalized_weights.values()) or sum(
        normalized_weights.values(), Decimal("0")
    ) != Decimal("1"):
        raise ConfigError("CONFIG_VALUE_INVALID: score_weights must be nonnegative and sum to 1")
    result["score_weights"] = normalized_weights
    if not result.get("csi300_etf_id"):
        raise ConfigError("CONFIG_VALUE_INVALID: csi300_etf_id is required")
    if cast(int, result["exit_rank"]) <= cast(int, result["entry_rank"]):
        raise ConfigError("CONFIG_VALUE_INVALID: exit_rank must be greater than entry_rank")
    if cast(int, result["max_holding_weeks"]) < cast(int, result["min_holding_weeks"]):
        raise ConfigError("CONFIG_VALUE_INVALID: max_holding_weeks must not be smaller")
    minimum_weight = cast(Decimal, result["single_stock_min_weight"])
    maximum_weight = cast(Decimal, result["single_stock_max_weight"])
    if minimum_weight > maximum_weight:
        raise ConfigError("CONFIG_VALUE_INVALID: single_stock minimum exceeds maximum")
    name = result["custom_factor_name"]
    factor_weight = cast(Decimal, result["custom_factor_weight"])
    if name is None and factor_weight != 0:
        raise ConfigError("CONFIG_VALUE_INVALID: custom_factor_weight must be 0 when disabled")
    if name is not None and not (Decimal("0") < factor_weight <= Decimal("0.20")):
        raise ConfigError("CONFIG_VALUE_INVALID: custom_factor_weight must be in (0, 0.20]")
    return result


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
    strategy_raw = raw.get("strategy", {})
    if not isinstance(strategy_raw, Mapping):
        raise ConfigError("CONFIG_SCHEMA_INVALID: strategy must be a table")
    parameters = _normalize_strategy(strategy_raw, cli_values)
    execution_raw = raw.get("execution", {})
    if not isinstance(execution_raw, Mapping):
        raise ConfigError("CONFIG_SCHEMA_INVALID: execution must be a table")
    execution = dict(_EXECUTION_DEFAULTS)
    execution.update(execution_raw)
    execution["slippage_bps"] = _decimal(execution["slippage_bps"], "slippage_bps")
    execution["initial_cash"] = _decimal(execution["initial_cash"], "initial_cash")
    execution["max_volume_participation"] = _decimal(
        execution["max_volume_participation"], "max_volume_participation"
    )
    if cast(Decimal, execution["initial_cash"]) <= 0:
        raise ConfigError("CONFIG_VALUE_INVALID: initial_cash must be positive")
    if not Decimal("0") < cast(Decimal, execution["max_volume_participation"]) <= 1:
        raise ConfigError("CONFIG_VALUE_INVALID: max_volume_participation must be in (0,1]")
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
    return ResolvedRunContext(
        run_id=run_id,
        mode=mode,
        strategy_id=str(raw.get("strategy_id", "weekly_market_guard_rank_v1")),
        strategy_version=str(raw.get("strategy_version", "1.0.0")),
        parameters=parameters,
        research_artifact_path=research,
        research_artifact_sha256=_manifest_sha(research),
        custom_factor_inputs=tuple(custom_inputs),
        decision_date=decision,
        start_date=start,
        end_date=end,
        account_snapshot_path=(
            Path(str(raw["account_snapshot"])).resolve() if raw.get("account_snapshot") else None
        ),
        previous_target_path=(
            Path(str(raw["previous_target"])).resolve() if raw.get("previous_target") else None
        ),
        output_path=output,
        execution_assumptions=execution,
        generated_at=generated_at,
    )
