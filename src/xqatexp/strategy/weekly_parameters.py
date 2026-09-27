from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import cast


WEEKLY_STRATEGY_DEFAULTS: dict[str, object] = {
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


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be numeric")
    try:
        return Decimal(str(value))
    except Exception as error:
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be numeric") from error


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be an integer")
    try:
        normalized = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be an integer") from error
    if str(normalized) != str(value):
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be an integer")
    return normalized


def normalize_weekly_parameters(
    raw: Mapping[str, object], cli: Mapping[str, object]
) -> dict[str, object]:
    unknown = set(raw) - (set(WEEKLY_STRATEGY_DEFAULTS) | {"csi300_etf_id"})
    if unknown:
        raise ValueError(f"CONFIG_SCHEMA_INVALID: unknown strategy field {min(unknown)}")
    result = dict(WEEKLY_STRATEGY_DEFAULTS)
    default_weights = cast(Mapping[str, object], WEEKLY_STRATEGY_DEFAULTS["score_weights"])
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
        raise ValueError("CONFIG_VALUE_INVALID: score_weights must contain exactly six keys")
    normalized_weights = {
        key: _decimal(value, f"score_weights.{key}") for key, value in weights.items()
    }
    if any(weight < 0 for weight in normalized_weights.values()) or sum(
        normalized_weights.values(), Decimal("0")
    ) != Decimal("1"):
        raise ValueError("CONFIG_VALUE_INVALID: score_weights must be nonnegative and sum to 1")
    result["score_weights"] = normalized_weights
    if not result.get("csi300_etf_id"):
        raise ValueError("CONFIG_VALUE_INVALID: csi300_etf_id is required")
    if cast(int, result["exit_rank"]) <= cast(int, result["entry_rank"]):
        raise ValueError("CONFIG_VALUE_INVALID: exit_rank must be greater than entry_rank")
    if cast(int, result["max_holding_weeks"]) < cast(int, result["min_holding_weeks"]):
        raise ValueError("CONFIG_VALUE_INVALID: max_holding_weeks must not be smaller")
    minimum_weight = cast(Decimal, result["single_stock_min_weight"])
    maximum_weight = cast(Decimal, result["single_stock_max_weight"])
    if minimum_weight > maximum_weight:
        raise ValueError("CONFIG_VALUE_INVALID: single_stock minimum exceeds maximum")
    name = result["custom_factor_name"]
    factor_weight = cast(Decimal, result["custom_factor_weight"])
    if name is None and factor_weight != 0:
        raise ValueError("CONFIG_VALUE_INVALID: custom_factor_weight must be 0 when disabled")
    if name is not None and not (Decimal("0") < factor_weight <= Decimal("0.20")):
        raise ValueError("CONFIG_VALUE_INVALID: custom_factor_weight must be in (0, 0.20]")
    return result
