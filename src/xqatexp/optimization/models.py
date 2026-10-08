from __future__ import annotations

import hashlib
import math
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise, product
from pathlib import Path
from typing import Any

from xqatexp.artifacts.manifest import canonical_json_bytes


def numeric_canonical(value: Any) -> Any:
    """Make equivalent numeric spellings share an identity."""
    if isinstance(value, (float, Decimal)):
        return Decimal(str(value)).normalize()
    if isinstance(value, Mapping):
        return {key: numeric_canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [numeric_canonical(item) for item in value]
    return value


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(numeric_canonical(value))).hexdigest()


def number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


@dataclass(frozen=True)
class Constraint:
    metric: str
    operator: str
    value: Decimal

    def accepts(self, metrics: Mapping[str, object]) -> bool:
        actual = metrics.get(self.metric)
        if number(actual) is None:
            return False
        left = Decimal(str(actual))
        return {
            ">": left > self.value,
            ">=": left >= self.value,
            "<": left < self.value,
            "<=": left <= self.value,
            "==": left == self.value,
        }[self.operator]


@dataclass(frozen=True)
class SearchSpace:
    parameters: Mapping[str, list[object]]
    ordered_arrays: Mapping[str, list[list[object]]]

    def generate(
        self,
        base: Mapping[str, object],
        normalize: Callable[[Mapping[str, object]], dict[str, object]],
    ) -> list[dict[str, Any]]:
        dimensions = dict(self.parameters)
        for key, components in self.ordered_arrays.items():
            dimensions[key] = [
                list(values)
                for values in product(*components)
                if all(Decimal(str(left)) < Decimal(str(right)) for left, right in pairwise(values))
            ]
        names = sorted(dimensions)
        unique: dict[str, dict[str, Any]] = {}
        for values in product(*(dimensions[name] for name in names)):
            parameters = normalize({**base, **dict(zip(names, values, strict=True))})
            identifier = digest(parameters)
            unique[identifier] = {"id": identifier, "parameters": parameters}
        if not unique:
            raise ValueError("OPT_CONFIG_INVALID: search space is empty")
        return [unique[key] for key in sorted(unique)]


@dataclass(frozen=True)
class StudySpec:
    space: SearchSpace
    fixed_strategy: Mapping[str, object]
    metric: str
    direction: str
    constraints: tuple[Constraint, ...]
    workers: int
    start_date: date | None
    end_date: date | None

    @classmethod
    def load(cls, path: Path) -> StudySpec:
        try:
            raw = tomllib.loads(path.read_text("utf-8"), parse_float=Decimal)
        except (OSError, ValueError) as error:
            raise ValueError(f"OPT_CONFIG_INVALID: cannot read TOML: {error}") from error
        allowed = {
            "schema_version",
            "method",
            "start_date",
            "end_date",
            "workers",
            "objective",
            "constraints",
            "fixed_strategy",
            "parameters",
            "ordered_arrays",
        }
        if set(raw) - allowed or raw.get("schema_version") != "1.0":
            raise ValueError("OPT_CONFIG_INVALID: unknown field or schema_version")
        if raw.get("method") != "grid":
            raise ValueError("OPT_CONFIG_INVALID: method must be grid")
        objective = raw.get("objective", {})
        if not isinstance(objective, dict) or set(objective) != {"metric", "direction"}:
            raise ValueError("OPT_CONFIG_INVALID: objective requires metric and direction")
        if not isinstance(objective["metric"], str) or not objective["metric"]:
            raise ValueError("OPT_CONFIG_INVALID: objective metric must be a string")
        if objective["direction"] not in ("maximize", "minimize"):
            raise ValueError("OPT_CONFIG_INVALID: invalid objective direction")
        workers = raw.get("workers", 16)
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError("OPT_CONFIG_INVALID: workers must be a positive integer")
        constraints = []
        items = raw.get("constraints", [])
        if not isinstance(items, list):
            raise ValueError("OPT_CONFIG_INVALID: constraints must be an array of tables")
        for item in items:
            if not isinstance(item, dict) or set(item) != {"metric", "operator", "value"}:
                raise ValueError("OPT_CONFIG_INVALID: invalid constraint fields")
            if not isinstance(item["metric"], str) or not item["metric"]:
                raise ValueError("OPT_CONFIG_INVALID: invalid constraint metric")
            if item["operator"] not in (">", ">=", "<", "<=", "=="):
                raise ValueError("OPT_CONFIG_INVALID: invalid constraint operator")
            if number(item["value"]) is None:
                raise ValueError("OPT_CONFIG_INVALID: constraint value must be finite numeric")
            constraints.append(
                Constraint(item["metric"], item["operator"], Decimal(str(item["value"])))
            )
        parameters = raw.get("parameters", {})
        arrays = raw.get("ordered_arrays", {})
        fixed = raw.get("fixed_strategy", {})
        if not all(isinstance(value, dict) for value in (parameters, arrays, fixed)):
            raise ValueError("OPT_CONFIG_INVALID: parameter sections must be tables")
        if not parameters and not arrays:
            raise ValueError("OPT_CONFIG_INVALID: search parameters are required")
        if set(parameters) & set(arrays) or (set(parameters) | set(arrays)) & set(fixed):
            raise ValueError("OPT_CONFIG_INVALID: search and fixed fields overlap")
        for values in parameters.values():
            if not isinstance(values, list) or not values:
                raise ValueError("OPT_CONFIG_INVALID: candidates must be nonempty arrays")
        for components in arrays.values():
            if not isinstance(components, list) or not components:
                raise ValueError("OPT_CONFIG_INVALID: ordered arrays require components")
            for values in components:
                if (
                    not isinstance(values, list)
                    or not values
                    or any(number(v) is None for v in values)
                ):
                    raise ValueError(
                        "OPT_CONFIG_INVALID: ordered candidates must be finite numeric"
                    )

        def optional_date(key: str) -> date | None:
            value = raw.get(key)
            return None if value is None else date.fromisoformat(str(value))

        return cls(
            SearchSpace(parameters, arrays),
            fixed,
            objective["metric"],
            objective["direction"],
            tuple(constraints),
            workers,
            optional_date("start_date"),
            optional_date("end_date"),
        )

    def rejection_reasons(self, metrics: Mapping[str, object]) -> list[str]:
        reasons = []
        if number(metrics.get(self.metric)) is None:
            reasons.append(f"{self.metric} is missing or non-finite")
        reasons.extend(
            f"{item.metric} {item.operator} {item.value} failed"
            for item in self.constraints
            if not item.accepts(metrics)
        )
        return reasons

    def rank_key(self, result: TrialResult) -> tuple[float, float, float, str]:
        objective = number(result.metrics.get(self.metric))
        if objective is None:
            raise ValueError("OPT_RESULT_INVALID: cannot rank invalid objective")
        annual = number(result.metrics.get("annualized_return")) or 0.0
        drawdown = number(result.metrics.get("max_drawdown")) or 0.0
        return (
            -objective if self.direction == "maximize" else objective,
            -annual,
            abs(drawdown),
            result.id,
        )


@dataclass(frozen=True)
class TrialResult:
    id: str
    parameters: Mapping[str, object]
    metrics: Mapping[str, object]
    status: str
    reasons: tuple[str, ...] = ()
    artifact_sha256: str | None = None
