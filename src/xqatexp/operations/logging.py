from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from xqatexp.security import SecretValue, assert_no_secret, redact


class JsonlEventLogger:
    """Append minimal, redacted operational events to one explicit local file."""

    def __init__(
        self,
        path: Path,
        run_id: str,
        *,
        known_secrets: Sequence[SecretValue] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = path.resolve()
        self._run_id = run_id
        self._known_secrets = tuple(known_secrets)
        self._clock = clock or (lambda: datetime.now(UTC))

    def emit(
        self,
        *,
        level: str,
        stage: str,
        event: str,
        message: str,
        context: Mapping[str, object] | None = None,
    ) -> None:
        value = {
            "timestamp": self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "level": level,
            "run_id": self._run_id,
            "stage": stage,
            "event": event,
            "message": message,
            "context": redact(dict(context or {}), self._known_secrets),
        }
        payload = (
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        assert_no_secret(payload, self._known_secrets)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as stream:
            stream.write(payload)
