from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.domain.enums import OverwritePolicy


class ArtifactPublishError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PublishedArtifact:
    path: Path
    manifest_sha256: str
    files: tuple[str, ...]


class ArtifactPublisher:
    def __init__(self, reader: ArtifactReader | None = None) -> None:
        self._reader = reader or ArtifactReader()

    def publish(
        self,
        staging_builder: Callable[[Path], None],
        output_path: Path,
        overwrite: OverwritePolicy,
    ) -> PublishedArtifact:
        target = output_path.resolve()
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        self._recover(target)
        if target.exists() and overwrite is OverwritePolicy.ERROR:
            raise ArtifactPublishError(f"ARTIFACT_OUTPUT_EXISTS: {target.name}")
        if target.exists() and overwrite is OverwritePolicy.SKIP:
            return self._published(target)

        run_suffix = uuid.uuid4().hex
        staging = parent / f".{target.name}.staging-{run_suffix}"
        backup = parent / f".{target.name}.backup-{run_suffix}"
        swap = parent / f".{target.name}.swap-{run_suffix}.json"
        staging.mkdir()
        try:
            staging_builder(staging)
            self._reader.open(staging)
            if not target.exists():
                staging.rename(target)
                return self._published(target)
            if overwrite is not OverwritePolicy.OVERWRITE:
                raise ArtifactPublishError(f"ARTIFACT_OUTPUT_EXISTS: {target.name}")
            swap.write_bytes(
                canonical_json_bytes(
                    {
                        "backup": backup.name,
                        "stage": "PREPARED",
                        "staging": staging.name,
                        "target": target.name,
                    }
                )
            )
            target.rename(backup)
            try:
                staging.rename(target)
                published = self._published(target)
            except Exception:
                if not target.exists() and backup.exists():
                    backup.rename(target)
                raise
            swap.unlink(missing_ok=True)
            shutil.rmtree(backup)
            return published
        except ArtifactPublishError:
            raise
        except Exception as error:
            raise ArtifactPublishError(f"ARTIFACT_PUBLISH_FAILED: {error}") from error
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _published(self, target: Path) -> PublishedArtifact:
        opened = self._reader.open(target)
        manifest_bytes = (target / "manifest.json").read_bytes()
        return PublishedArtifact(
            target,
            hashlib.sha256(manifest_bytes).hexdigest(),
            opened.verified_files,
        )

    def _recover(self, target: Path) -> None:
        prefix = f".{target.name}.swap-"
        records = sorted(
            child
            for child in target.parent.iterdir()
            if child.is_file() and child.name.startswith(prefix) and child.name.endswith(".json")
        )
        for record_path in records:
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if record.get("target") != target.name:
                    continue
                backup = self._validated_sibling(target, record["backup"], ".backup-")
                staging = self._validated_sibling(target, record["staging"], ".staging-")
                backup_valid = backup.exists() and self._is_valid(backup)
                target_valid = target.exists() and self._is_valid(target)
                if not target.exists() and backup_valid:
                    backup.rename(target)
                    target_valid = True
                if target_valid:
                    if backup.exists():
                        shutil.rmtree(backup)
                    if staging.exists():
                        shutil.rmtree(staging)
                    record_path.unlink()
            except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
                raise ArtifactPublishError(
                    f"ARTIFACT_PUBLISH_FAILED: invalid recovery record {record_path.name}: {error}"
                ) from error

    @staticmethod
    def _validated_sibling(target: Path, name: str, marker: str) -> Path:
        if not isinstance(name, str) or not name.startswith(f".{target.name}{marker}"):
            raise ValueError("recovery member name does not match target")
        sibling = (target.parent / name).resolve()
        if sibling.parent != target.parent:
            raise ValueError("recovery member escapes target parent")
        return sibling

    def _is_valid(self, path: Path) -> bool:
        try:
            self._reader.open(path)
        except Exception:
            return False
        return True
