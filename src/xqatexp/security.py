from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SENSITIVE_KEY = re.compile(r"token|secret|password|credential|authorization", re.IGNORECASE)


class SecurityError(RuntimeError):
    """A credential or output-safety boundary was violated."""


@dataclass(frozen=True, slots=True)
class SecretValue:
    _value: str

    def __post_init__(self) -> None:
        if not self._value:
            raise ValueError("secret must not be empty")

    def __str__(self) -> str:
        return "<redacted>"

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    def reveal(self) -> str:
        return self._value


def load_tushare_token(environ: Mapping[str, str]) -> SecretValue:
    value = environ.get("TUSHARE_TOKEN", "").strip()
    if not value:
        raise SecurityError(
            "SECURITY_SECRET_MISSING: set TUSHARE_TOKEN in the current process environment"
        )
    return SecretValue(value)


def _contains_secret_fragment(value: str, secrets: Sequence[SecretValue]) -> bool:
    for secret in secrets:
        raw = secret.reveal()
        if raw in value:
            return True
        for start in range(max(0, len(raw) - 7)):
            if raw[start : start + 8] in value:
                return True
    return False


def redact(value: Any, known_secrets: Sequence[SecretValue] = ()) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "<redacted>" if _SENSITIVE_KEY.search(str(key)) else redact(item, known_secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, known_secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item, known_secrets) for item in value)
    if isinstance(value, SecretValue):
        return "<redacted>"
    if isinstance(value, str) and _contains_secret_fragment(value, known_secrets):
        return "<redacted>"
    return value


def assert_no_secret(material: str | bytes, known_secrets: Sequence[SecretValue]) -> None:
    text = material.decode("utf-8", errors="replace") if isinstance(material, bytes) else material
    if _contains_secret_fragment(text, known_secrets):
        raise SecurityError("SECURITY_SECRET_EXPOSURE_BLOCKED: output contains credential material")


def validate_disjoint_paths(inputs: Mapping[str, Path], outputs: Mapping[str, Path]) -> None:
    """Reject any output that equals, contains, or is contained by an input/output."""
    normalized_inputs = {name: path.resolve() for name, path in inputs.items()}
    normalized_outputs = {name: path.resolve() for name, path in outputs.items()}

    def overlaps(left: Path, right: Path) -> bool:
        return left == right or left in right.parents or right in left.parents

    for output_name, output in normalized_outputs.items():
        for input_name, input_path in normalized_inputs.items():
            if overlaps(output, input_path):
                raise ValueError(
                    f"CONFIG_PATH_CONFLICT: output {output_name} overlaps input {input_name}"
                )
        for other_name, other_output in normalized_outputs.items():
            if output_name < other_name and overlaps(output, other_output):
                raise ValueError(
                    f"CONFIG_PATH_CONFLICT: outputs {output_name} and {other_name} overlap"
                )
