from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _encode(value: object, level: int) -> str:
    indent = "  " * level
    child_indent = "  " * (level + 1)
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, Enum):
        return _encode(value.value, level)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("JSON Decimal must be finite")
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON float must be finite")
        return repr(0.0 if value == 0.0 else value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("JSON datetime must include a timezone")
        utc_value = value.astimezone(UTC)
        rendered = utc_value.isoformat().replace("+00:00", "Z")
        return _string(rendered)
    if isinstance(value, date):
        return _string(value.isoformat())
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        lines = []
        for key in sorted(value):
            lines.append(f"{child_indent}{_string(key)}: {_encode(value[key], level + 1)}")
        return "{\n" + ",\n".join(lines) + f"\n{indent}}}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return "[]"
        lines = [f"{child_indent}{_encode(item, level + 1)}" for item in value]
        return "[\n" + ",\n".join(lines) + f"\n{indent}]"
    raise TypeError(f"unsupported JSON type: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    return (_encode(value, 0) + "\n").encode("utf-8")
