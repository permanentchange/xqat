from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.domain.numeric import normalize_factor, quantize_fen


@dataclass(frozen=True, slots=True)
class SelfCheckResult:
    checks: tuple[str, ...]


class SelfCheckService:
    def run(self, *, offline: bool) -> SelfCheckResult:
        if not offline:
            raise ValueError("self-check currently requires --offline")
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError("CONFIG_VALUE_INVALID: CPython 3.12 is required")
        registry = SchemaRegistry()
        for schema_id in registry.json_schema_ids:
            registry.load_json_schema(schema_id)
        with tempfile.TemporaryDirectory(prefix="xqatexp-self-check-") as directory:
            probe = Path(directory) / "write-probe"
            probe.write_text("ok\n", encoding="utf-8")
            if probe.read_text(encoding="utf-8") != "ok\n":
                raise RuntimeError("ARTIFACT_PUBLISH_FAILED: temporary write probe failed")
        if quantize_fen(Decimal("1.005")) != Decimal("1.01"):
            raise RuntimeError("CONFIG_VALUE_INVALID: Decimal rounding self-check failed")
        if normalize_factor(-0.0) != 0.0:
            raise RuntimeError("FACTOR_VALUE_NONFINITE: factor normalization self-check failed")
        return SelfCheckResult(("python", "schemas", "temporary_write", "numeric_golden"))
