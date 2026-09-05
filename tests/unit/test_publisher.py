from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublisher, ArtifactPublishError
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.domain.enums import OverwritePolicy


def _builder(payload: bytes):
    def build(staging: Path) -> None:
        (staging / "payload.txt").write_bytes(payload)
        manifest = {
            "schema_version": "1.0",
            "artifact_type": "RAW_DATA",
            "artifact_id": "01991a6a-4c00-7000-8000-000000000001",
            "created_at": "2026-09-05T00:00:00Z",
            "producer": {"name": "xqatexp", "version": "0.1.0"},
            "run": None,
            "inputs": [],
            "date_scope": {"start": None, "end": None},
            "files": [
                {
                    "path": "payload.txt",
                    "media_type": "text/plain",
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "row_count": None,
                    "schema_id": None,
                    "schema_version": None,
                }
            ],
            "issues": [],
            "limitations": [],
        }
        (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

    return build


def test_existing_error_does_not_call_builder_or_change_artifact(tmp_path: Path) -> None:
    """Catches eager staging work or mutation before existing-target rejection."""
    target = tmp_path / "result"
    publisher = ArtifactPublisher()
    publisher.publish(_builder(b"old\n"), target, OverwritePolicy.ERROR)
    called = False

    def forbidden_builder(_: Path) -> None:
        nonlocal called
        called = True

    with pytest.raises(ArtifactPublishError, match="ARTIFACT_OUTPUT_EXISTS"):
        publisher.publish(forbidden_builder, target, OverwritePolicy.ERROR)

    assert not called
    assert (target / "payload.txt").read_bytes() == b"old\n"


def test_explicit_overwrite_replaces_only_after_new_artifact_verifies(tmp_path: Path) -> None:
    """Catches in-place truncation or an overwrite that leaves stale files."""
    target = tmp_path / "result"
    publisher = ArtifactPublisher()
    publisher.publish(_builder(b"old\n"), target, OverwritePolicy.ERROR)
    publisher.publish(_builder(b"new\n"), target, OverwritePolicy.OVERWRITE)

    opened = ArtifactReader().open(target)
    assert opened.verified_files == ("payload.txt",)
    assert (target / "payload.txt").read_bytes() == b"new\n"
    assert not tuple(tmp_path.glob(".result.backup-*"))
    assert not tuple(tmp_path.glob(".result.swap-*.json"))


def test_startup_recovers_verified_backup_before_new_publish(tmp_path: Path) -> None:
    """Catches a process restart that abandons the last verified artifact after rename."""
    target = tmp_path / "result"
    publisher = ArtifactPublisher()
    publisher.publish(_builder(b"old\n"), target, OverwritePolicy.ERROR)

    backup = tmp_path / ".result.backup-fixed"
    staging = tmp_path / ".result.staging-fixed"
    swap = tmp_path / ".result.swap-fixed.json"
    target.rename(backup)
    staging.mkdir()
    swap.write_bytes(
        canonical_json_bytes(
            {
                "backup": backup.name,
                "stage": "OLD_RENAMED",
                "staging": staging.name,
                "target": target.name,
            }
        )
    )

    with pytest.raises(ArtifactPublishError, match="ARTIFACT_OUTPUT_EXISTS"):
        publisher.publish(_builder(b"unexpected\n"), target, OverwritePolicy.ERROR)

    assert ArtifactReader().open(target).verified_files == ("payload.txt",)
    assert (target / "payload.txt").read_bytes() == b"old\n"
    assert not backup.exists()
    assert not staging.exists()
    assert not swap.exists()
