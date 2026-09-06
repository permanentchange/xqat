from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_HEADER = ("factor_name", "security_id", "factor_date", "factor_value")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CustomFactorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CsvCustomFactorView:
    decision_date: date
    _required: frozenset[str]
    _values: dict[tuple[str, str, date], float]

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        decision_date: date,
        known_security_ids: set[str],
        required_factor_ids: set[str],
    ) -> CsvCustomFactorView:
        values: dict[tuple[str, str, date], float] = {}
        try:
            with path.open("r", encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                if tuple(reader.fieldnames or ()) != _HEADER:
                    raise CustomFactorError("FACTOR_SCHEMA_INVALID: exact CSV header required")
                for row in reader:
                    name = row["factor_name"]
                    security_id = row["security_id"]
                    if not _NAME.fullmatch(name) or security_id not in known_security_ids:
                        raise CustomFactorError("FACTOR_SCHEMA_INVALID: invalid factor or security")
                    try:
                        factor_date = date.fromisoformat(row["factor_date"])
                        value = float(row["factor_value"])
                    except ValueError as error:
                        raise CustomFactorError(
                            "FACTOR_SCHEMA_INVALID: invalid date or value"
                        ) from error
                    if not math.isfinite(value):
                        raise CustomFactorError("FACTOR_VALUE_NONFINITE: value must be finite")
                    key = (name, security_id, factor_date)
                    if key in values:
                        raise CustomFactorError(f"FACTOR_DUPLICATE_KEY: {key}")
                    values[key] = 0.0 if value == 0.0 else value
        except (OSError, UnicodeError) as error:
            raise CustomFactorError(
                f"FACTOR_SCHEMA_INVALID: cannot read factor file: {error}"
            ) from error
        missing_names = required_factor_ids - {key[0] for key in values}
        if missing_names:
            raise CustomFactorError(
                f"FACTOR_COVERAGE_INSUFFICIENT: missing {','.join(sorted(missing_names))}"
            )
        return cls(decision_date, frozenset(required_factor_ids), values)

    def values_at(
        self,
        factor_date: date,
        factor_ids: tuple[str, ...],
        security_ids: tuple[str, ...],
    ) -> dict[str, dict[str, float]]:
        if factor_date > self.decision_date:
            raise CustomFactorError("STRATEGY_HISTORY_SLICE_VIOLATION: future factor date")
        if not set(factor_ids).issubset(self._required):
            raise CustomFactorError("FACTOR_SCHEMA_INVALID: undeclared factor")
        return {
            factor_id: {
                security_id: self._values[(factor_id, security_id, factor_date)]
                for security_id in sorted(security_ids)
                if (factor_id, security_id, factor_date) in self._values
            }
            for factor_id in sorted(factor_ids)
        }
