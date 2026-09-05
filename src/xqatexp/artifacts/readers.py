from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from xqatexp.artifacts.schemas import SchemaRegistry


class ArtifactReadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OpenedArtifact:
    path: Path
    manifest: dict[str, Any]
    verified_files: tuple[str, ...]


def _safe_member(root: Path, member: str) -> Path:
    relative = PurePosixPath(member)
    if relative.is_absolute() or ".." in relative.parts or member != relative.as_posix():
        raise ArtifactReadError(f"DATA_INPUT_CORRUPT: unsafe artifact member {member!r}")
    resolved = (root / Path(*relative.parts)).resolve()
    if root.resolve() not in resolved.parents:
        raise ArtifactReadError(f"DATA_INPUT_CORRUPT: escaped artifact member {member!r}")
    return resolved


class ArtifactReader:
    def __init__(self, schemas: SchemaRegistry | None = None) -> None:
        self._schemas = schemas or SchemaRegistry()

    def open(self, path: Path) -> OpenedArtifact:
        root = path.resolve()
        manifest_path = root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"), parse_float=Decimal)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ArtifactReadError(f"DATA_INPUT_CORRUPT: cannot read manifest: {error}") from error
        self._schemas.validate_json("artifact_manifest", manifest)
        verified = []
        for entry in manifest["files"]:
            member = _safe_member(root, entry["path"])
            try:
                payload = member.read_bytes()
            except OSError as error:
                raise ArtifactReadError(
                    f"ARTIFACT_HASH_MISMATCH: missing {entry['path']}"
                ) from error
            digest = hashlib.sha256(payload).hexdigest()
            if len(payload) != entry["size_bytes"] or digest != entry["sha256"]:
                raise ArtifactReadError(f"ARTIFACT_HASH_MISMATCH: {entry['path']}")
            verified.append(entry["path"])
        return OpenedArtifact(root, manifest, tuple(verified))
