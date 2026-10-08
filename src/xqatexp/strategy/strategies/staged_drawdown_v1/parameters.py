from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import cast

STAGED_DRAWDOWN_DEFAULTS: dict[str, object] = {
    "lookback_trade_days": 20,
    "cumulative_decline_threshold": Decimal("0.10"),
    "single_day_crash_threshold": Decimal("0.05"),
    "minimum_down_days": 12,
    "add_buy_decline_threshold": Decimal("0.10"),
    "buy_fraction": Decimal("0.10"),
    "max_capital_fraction": Decimal("1.00"),
    "take_profit_threshold": Decimal("0.10"),
    "sell_fraction": Decimal("0.20"),
    "take_profit_mode": "repeat",
    "take_profit_levels": (Decimal("0.10"), Decimal("0.20"), Decimal("0.30")),
    "take_profit_sell_fractions": (Decimal("0.30"), Decimal("0.30"), Decimal("0.40")),
}


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be numeric")
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be numeric") from error
    if not result.is_finite():
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be finite")
    return result


def _decimal_array(value: object, field: str) -> tuple[Decimal, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be a nonempty array")
    return tuple(_decimal(item, field) for item in value)


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be an integer")
    try:
        result = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be an integer") from error
    if str(result) != str(value):
        raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be an integer")
    return result


def normalize_staged_drawdown_parameters(
    raw: Mapping[str, object],
    cli: Mapping[str, object],
) -> dict[str, object]:
    allowed = set(STAGED_DRAWDOWN_DEFAULTS) | {"security_id"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"CONFIG_SCHEMA_INVALID: unknown strategy field {min(unknown)}")
    result = dict(STAGED_DRAWDOWN_DEFAULTS)
    result.update(raw)
    for key in allowed:
        if key in cli and cli[key] is not None:
            result[key] = cli[key]

    mode = result["take_profit_mode"]
    if mode not in ("repeat", "tiered"):
        raise ValueError("CONFIG_VALUE_INVALID: take_profit_mode must be repeat or tiered")
    if mode == "tiered" and any(
        key in raw or cli.get(key) is not None for key in ("take_profit_threshold", "sell_fraction")
    ):
        raise ValueError(
            "CONFIG_VALUE_INVALID: tiered mode conflicts with repeat profit parameters"
        )
    levels = _decimal_array(result["take_profit_levels"], "take_profit_levels")
    fractions = _decimal_array(result["take_profit_sell_fractions"], "take_profit_sell_fractions")
    if len(levels) != len(fractions):
        raise ValueError("CONFIG_VALUE_INVALID: take profit arrays must have equal length")
    if any(level <= 0 for level in levels) or any(
        left >= right for left, right in pairwise(levels)
    ):
        raise ValueError("CONFIG_VALUE_INVALID: take_profit_levels must be positive and increasing")
    if any(not 0 < fraction <= 1 for fraction in fractions) or sum(fractions) != 1:
        raise ValueError("CONFIG_VALUE_INVALID: take_profit_sell_fractions must be positive, sum=1")
    result["take_profit_levels"] = levels
    result["take_profit_sell_fractions"] = fractions

    security_id = str(result.get("security_id", "")).strip()
    if not security_id:
        raise ValueError("CONFIG_VALUE_INVALID: security_id is required")
    if re.fullmatch(r"[0-9]{6}\.(SH|SZ)", security_id) is None:
        raise ValueError("CONFIG_VALUE_INVALID: security_id must be 000000.SH/SZ")
    result["security_id"] = security_id

    result["lookback_trade_days"] = _integer(result["lookback_trade_days"], "lookback_trade_days")
    result["minimum_down_days"] = _integer(result["minimum_down_days"], "minimum_down_days")
    if cast(int, result["lookback_trade_days"]) < 2:
        raise ValueError("CONFIG_VALUE_INVALID: lookback_trade_days must be at least 2")
    if not 1 <= cast(int, result["minimum_down_days"]) < cast(int, result["lookback_trade_days"]):
        raise ValueError("CONFIG_VALUE_INVALID: minimum_down_days is outside lookback")

    for field in (
        "cumulative_decline_threshold",
        "single_day_crash_threshold",
        "add_buy_decline_threshold",
        "buy_fraction",
        "max_capital_fraction",
        "take_profit_threshold",
        "sell_fraction",
    ):
        result[field] = _decimal(result[field], field)

    unit_interval_fields = (
        "cumulative_decline_threshold",
        "single_day_crash_threshold",
        "add_buy_decline_threshold",
        "buy_fraction",
        "max_capital_fraction",
        "take_profit_threshold",
        "sell_fraction",
    )
    for field in unit_interval_fields:
        value = cast(Decimal, result[field])
        if not Decimal("0") < value <= Decimal("1"):
            raise ValueError(f"CONFIG_VALUE_INVALID: {field} must be in (0,1]")

    if cast(Decimal, result["buy_fraction"]) > cast(Decimal, result["max_capital_fraction"]):
        raise ValueError("CONFIG_VALUE_INVALID: buy_fraction exceeds max_capital_fraction")
    if mode == "tiered":
        del result["take_profit_threshold"]
        del result["sell_fraction"]
    return result


@dataclass(frozen=True, slots=True)
class StagedDrawdownParameters:
    security_id: str
    lookback_trade_days: int
    cumulative_decline_threshold: Decimal
    single_day_crash_threshold: Decimal
    minimum_down_days: int
    add_buy_decline_threshold: Decimal
    buy_fraction: Decimal
    max_capital_fraction: Decimal
    take_profit_threshold: Decimal
    sell_fraction: Decimal
    take_profit_mode: str
    take_profit_levels: tuple[Decimal, ...]
    take_profit_sell_fractions: tuple[Decimal, ...]

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> StagedDrawdownParameters:
        normalized = normalize_staged_drawdown_parameters(values, {})
        return cls(
            security_id=str(normalized["security_id"]),
            lookback_trade_days=int(cast(int, normalized["lookback_trade_days"])),
            cumulative_decline_threshold=cast(Decimal, normalized["cumulative_decline_threshold"]),
            single_day_crash_threshold=cast(Decimal, normalized["single_day_crash_threshold"]),
            minimum_down_days=int(cast(int, normalized["minimum_down_days"])),
            add_buy_decline_threshold=cast(Decimal, normalized["add_buy_decline_threshold"]),
            buy_fraction=cast(Decimal, normalized["buy_fraction"]),
            max_capital_fraction=cast(Decimal, normalized["max_capital_fraction"]),
            take_profit_threshold=cast(
                Decimal, normalized.get("take_profit_threshold", Decimal("0.10"))
            ),
            sell_fraction=cast(Decimal, normalized.get("sell_fraction", Decimal("0.20"))),
            take_profit_mode=cast(str, normalized["take_profit_mode"]),
            take_profit_levels=cast(tuple[Decimal, ...], normalized["take_profit_levels"]),
            take_profit_sell_fractions=cast(
                tuple[Decimal, ...], normalized["take_profit_sell_fractions"]
            ),
        )

    def as_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "security_id": self.security_id,
            "lookback_trade_days": self.lookback_trade_days,
            "cumulative_decline_threshold": self.cumulative_decline_threshold,
            "single_day_crash_threshold": self.single_day_crash_threshold,
            "minimum_down_days": self.minimum_down_days,
            "add_buy_decline_threshold": self.add_buy_decline_threshold,
            "buy_fraction": self.buy_fraction,
            "max_capital_fraction": self.max_capital_fraction,
            "take_profit_threshold": self.take_profit_threshold,
            "sell_fraction": self.sell_fraction,
            "take_profit_mode": self.take_profit_mode,
            "take_profit_levels": self.take_profit_levels,
            "take_profit_sell_fractions": self.take_profit_sell_fractions,
        }
        if self.take_profit_mode == "tiered":
            del result["take_profit_threshold"]
            del result["sell_fraction"]
        return result
