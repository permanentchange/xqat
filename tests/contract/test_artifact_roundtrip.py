from __future__ import annotations

import hashlib
import importlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest


def _artifacts(name: str):
    try:
        return importlib.import_module(f"xqatexp.artifacts.{name}")
    except ModuleNotFoundError:
        pytest.fail(f"xqatexp.artifacts.{name} is not implemented", pytrace=False)


def test_canonical_json_preserves_decimal_and_sorts_keys() -> None:
    """Catches float conversion or unstable object-key ordering in artifacts."""
    manifest = _artifacts("manifest")
    value = {
        "z": date(2026, 9, 5),
        "amount": Decimal("1.20"),
        "at": datetime(2026, 9, 5, 1, 2, 3, tzinfo=UTC),
    }
    assert manifest.canonical_json_bytes(value) == (
        b'{\n  "amount": 1.20,\n  "at": "2026-09-05T01:02:03Z",\n  "z": "2026-09-05"\n}\n'
    )


def _raw_manifest(payload: bytes, path: str = "payload.txt") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "artifact_type": "RAW_DATA",
        "artifact_id": "01991a6a-4c00-7000-8000-000000000001",
        "created_at": "2026-09-05T00:00:00Z",
        "producer": {"name": "xqatexp", "version": "0.1.0"},
        "run": None,
        "inputs": [],
        "date_scope": {"start": "2026-09-01", "end": "2026-09-05"},
        "files": [
            {
                "path": path,
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


def test_publisher_and_reader_verify_real_file_digest(tmp_path: Path) -> None:
    """Catches publishers that announce success before validating written bytes."""
    manifest_module = _artifacts("manifest")
    publisher_module = _artifacts("publisher")
    readers_module = _artifacts("readers")
    enums = importlib.import_module("xqatexp.domain.enums")
    payload = b"verified payload\n"

    def build(staging: Path) -> None:
        (staging / "payload.txt").write_bytes(payload)
        (staging / "manifest.json").write_bytes(
            manifest_module.canonical_json_bytes(_raw_manifest(payload))
        )

    output = tmp_path / "raw-artifact"
    published = publisher_module.ArtifactPublisher().publish(
        build, output, enums.OverwritePolicy.ERROR
    )
    opened = readers_module.ArtifactReader().open(output)

    assert published.path == output.resolve()
    assert opened.manifest["artifact_type"] == "RAW_DATA"
    assert opened.verified_files == ("payload.txt",)


def test_publisher_and_reader_round_trip_unicode_space_path_and_nested_member(
    tmp_path: Path,
) -> None:
    """Catches host-native paths or nested Manifest members escaping portable form."""
    manifest_module = _artifacts("manifest")
    publisher_module = _artifacts("publisher")
    readers_module = _artifacts("readers")
    enums = importlib.import_module("xqatexp.domain.enums")
    payload = b"verified nested payload\n"

    def build(staging: Path) -> None:
        nested = staging / "nested"
        nested.mkdir()
        (nested / "payload.txt").write_bytes(payload)
        (staging / "manifest.json").write_bytes(
            manifest_module.canonical_json_bytes(_raw_manifest(payload, "nested/payload.txt"))
        )

    output = tmp_path / "跨平台 result"
    published = publisher_module.ArtifactPublisher().publish(
        build, output, enums.OverwritePolicy.ERROR
    )
    opened = readers_module.ArtifactReader().open(published.path)

    assert published.path == output.resolve()
    assert "nested/payload.txt" in opened.verified_files
    assert all("\\" not in member for member in opened.verified_files)


def test_reader_rejects_tampered_payload(tmp_path: Path) -> None:
    """Catches readers that trust Manifest metadata without hashing payloads."""
    manifest_module = _artifacts("manifest")
    readers_module = _artifacts("readers")
    payload = b"original\n"
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "payload.txt").write_bytes(b"tampered\n")
    (artifact / "manifest.json").write_bytes(
        manifest_module.canonical_json_bytes(_raw_manifest(payload))
    )

    with pytest.raises(Exception, match="ARTIFACT_HASH_MISMATCH"):
        readers_module.ArtifactReader().open(artifact)
