from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from xqatexp.operations.logging import JsonlEventLogger
from xqatexp.security import SecretValue


def test_jsonl_logger_writes_stable_fields_and_redacts_context(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    logger = JsonlEventLogger(
        output,
        "run-001",
        known_secrets=(SecretValue("abcdefgh12345678"),),
        clock=lambda: datetime(2026, 9, 6, 1, 2, 3, tzinfo=UTC),
    )

    logger.emit(
        level="INFO",
        stage="CLI",
        event="COMMAND_STARTED",
        message="started",
        context={"token": "anything", "detail": "contains-abcdefgh"},
    )

    row = json.loads(output.read_text(encoding="utf-8"))
    assert row == {
        "timestamp": "2026-09-06T01:02:03Z",
        "level": "INFO",
        "run_id": "run-001",
        "stage": "CLI",
        "event": "COMMAND_STARTED",
        "message": "started",
        "context": {"detail": "<redacted>", "token": "<redacted>"},
    }
    assert "abcdefgh" not in output.read_text(encoding="utf-8")
