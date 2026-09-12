from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

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


def test_publisher_rejects_filesystem_and_workspace_roots_before_building() -> None:
    publisher = ArtifactPublisher()

    def must_not_build(staging: Path) -> None:
        raise AssertionError(f"builder unexpectedly called for {staging}")

    for unsafe in (Path(Path.cwd().anchor), Path.cwd()):
        with pytest.raises(ArtifactPublishError, match="ARTIFACT_UNSAFE_OUTPUT_PATH"):
            publisher.publish(must_not_build, unsafe, OverwritePolicy.ERROR)


def test_publisher_rejects_symlink_without_resolving_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "linked-result"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == output or original(self))
    with pytest.raises(ArtifactPublishError, match="symlink or junction"):
        ArtifactPublisher().publish(_builder(b"new\n"), output, OverwritePolicy.ERROR)


def test_publisher_rejects_existing_parent_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked_parent = tmp_path / "linked-parent"
    linked_parent.mkdir()
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == linked_parent or original(self))
    with pytest.raises(ArtifactPublishError, match="symlink or junction"):
        ArtifactPublisher().publish(
            _builder(b"new\n"), linked_parent / "result", OverwritePolicy.ERROR
        )


def test_publisher_rejects_real_directory_symlink(tmp_path: Path) -> None:
    """Catches path validation that resolves a symlink before rejecting it."""
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable on this host: {error}")
    with pytest.raises(ArtifactPublishError, match="ARTIFACT_UNSAFE_OUTPUT_PATH"):
        ArtifactPublisher().publish(
            _builder(b"payload"), linked_parent / "result", OverwritePolicy.ERROR
        )


def test_publisher_rejects_low_disk_before_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher = ArtifactPublisher()
    monkeypatch.setattr(
        "xqatexp.artifacts.publisher.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=1024**3 - 1),
    )

    with pytest.raises(ArtifactPublishError, match="ARTIFACT_RESOURCE_INSUFFICIENT"):
        publisher.publish(
            lambda _staging: pytest.fail("builder must not run"),
            tmp_path / "result",
            OverwritePolicy.ERROR,
        )


def test_skip_returns_existing_verified_artifact(tmp_path: Path) -> None:
    target = tmp_path / "result"
    publisher = ArtifactPublisher()
    publisher.publish(_builder(b"old\n"), target, OverwritePolicy.ERROR)

    published = publisher.publish(
        lambda _staging: pytest.fail("builder must not run"), target, OverwritePolicy.SKIP
    )

    assert published.path == target.resolve()
    assert (target / "payload.txt").read_bytes() == b"old\n"


def test_skip_existing_does_not_require_write_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "result"
    publisher = ArtifactPublisher()
    publisher.publish(_builder(b"old\n"), target, OverwritePolicy.ERROR)
    monkeypatch.setattr(
        "xqatexp.artifacts.publisher.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=0),
    )
    assert publisher.publish(_builder(b"unused"), target, OverwritePolicy.SKIP).path == target


def test_publisher_rejects_disk_that_cannot_hold_twice_built_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    free_values = iter((2 * 1024**3, 0))
    monkeypatch.setattr(
        "xqatexp.artifacts.publisher.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=next(free_values)),
    )

    with pytest.raises(ArtifactPublishError, match="ARTIFACT_RESOURCE_INSUFFICIENT"):
        ArtifactPublisher().publish(_builder(b"new\n"), tmp_path / "result", OverwritePolicy.ERROR)
    assert not tuple(tmp_path.glob(".result.staging-*"))


def test_builder_exception_is_wrapped_and_staging_is_removed(tmp_path: Path) -> None:
    def broken_builder(_staging: Path) -> None:
        raise RuntimeError("controlled failure")

    with pytest.raises(ArtifactPublishError, match="ARTIFACT_PUBLISH_FAILED"):
        ArtifactPublisher().publish(broken_builder, tmp_path / "result", OverwritePolicy.ERROR)
    assert not tuple(tmp_path.glob(".result.staging-*"))


def test_invalid_recovery_member_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".result.swap-invalid.json").write_bytes(
        canonical_json_bytes(
            {
                "backup": "outside",
                "stage": "PREPARED",
                "staging": ".result.staging-valid",
                "target": "result",
            }
        )
    )

    with pytest.raises(ArtifactPublishError, match="invalid recovery record"):
        ArtifactPublisher().publish(_builder(b"new\n"), tmp_path / "result", OverwritePolicy.ERROR)


def test_recovery_ignores_other_target_and_rejects_escaping_member(tmp_path: Path) -> None:
    unrelated = tmp_path / ".result.swap-unrelated.json"
    unrelated.write_bytes(
        canonical_json_bytes(
            {
                "backup": ".other.backup-old",
                "stage": "PREPARED",
                "staging": ".other.staging-new",
                "target": "other",
            }
        )
    )
    ArtifactPublisher().publish(_builder(b"new\n"), tmp_path / "result", OverwritePolicy.ERROR)
    assert unrelated.exists()

    with pytest.raises(ValueError, match="escapes target parent"):
        ArtifactPublisher._validated_sibling(
            tmp_path / "result", ".result.backup-..\\escape", ".backup-"
        )


def test_recovery_discards_corrupt_backup_when_target_is_valid(tmp_path: Path) -> None:
    target = tmp_path / "result"
    publisher = ArtifactPublisher()
    publisher.publish(_builder(b"old\n"), target, OverwritePolicy.ERROR)
    backup = tmp_path / ".result.backup-corrupt"
    staging = tmp_path / ".result.staging-abandoned"
    backup.mkdir()
    (backup / "not-a-manifest").write_text("bad", encoding="utf-8")
    staging.mkdir()
    swap = tmp_path / ".result.swap-recover.json"
    swap.write_bytes(
        canonical_json_bytes(
            {
                "backup": backup.name,
                "stage": "NEW_RENAMED",
                "staging": staging.name,
                "target": target.name,
            }
        )
    )

    with pytest.raises(ArtifactPublishError, match="ARTIFACT_OUTPUT_EXISTS"):
        publisher.publish(_builder(b"new\n"), target, OverwritePolicy.ERROR)
    assert not backup.exists()
    assert not staging.exists()
    assert not swap.exists()


def test_recovery_restores_valid_backup_over_corrupt_target(tmp_path: Path) -> None:
    target = tmp_path / "result"
    publisher = ArtifactPublisher()
    publisher.publish(_builder(b"old\n"), target, OverwritePolicy.ERROR)
    backup = tmp_path / ".result.backup-good"
    staging = tmp_path / ".result.staging-abandoned"
    swap = tmp_path / ".result.swap-recover-corrupt.json"
    target.rename(backup)
    target.mkdir()
    (target / "corrupt.txt").write_text("bad", encoding="utf-8")
    staging.mkdir()
    swap.write_bytes(
        canonical_json_bytes(
            {
                "backup": backup.name,
                "stage": "NEW_RENAMED",
                "staging": staging.name,
                "target": target.name,
            }
        )
    )

    with pytest.raises(ArtifactPublishError, match="ARTIFACT_OUTPUT_EXISTS"):
        publisher.publish(_builder(b"new\n"), target, OverwritePolicy.ERROR)
    assert (target / "payload.txt").read_bytes() == b"old\n"
    assert not backup.exists()
    assert not staging.exists()
    assert not swap.exists()
