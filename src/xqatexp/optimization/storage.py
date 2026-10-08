from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublishError


def read_json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"), parse_float=Decimal)


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    atomic_write(path, canonical_json_bytes(value))


def safe_output(path: Path) -> Path:
    target = Path(os.path.abspath(path))
    if target == Path(target.anchor) or target == Path.cwd():
        raise ArtifactPublishError("OPT_UNSAFE_OUTPUT: root or repository directory")
    for component in (target, *target.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ArtifactPublishError("OPT_UNSAFE_OUTPUT: symlink or junction")
    return target


@contextmanager
def study_lock(output: Path) -> Iterator[None]:
    output = safe_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.opt.lock"
    if lock_path.is_symlink():
        raise ArtifactPublishError("OPT_UNSAFE_OUTPUT: symlink lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                cast(Any, msvcrt).locking(handle.fileno(), cast(Any, msvcrt).LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ArtifactPublishError(
                "OPT_OUTPUT_LOCKED: another coordinator is running"
            ) from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                cast(Any, msvcrt).locking(handle.fileno(), cast(Any, msvcrt).LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def prepare_output(output: Path, existing: str, resume: bool) -> Path | None:
    """Retain overwritten output until its replacement has completed successfully."""
    if output.exists():
        if not output.is_dir():
            raise ArtifactPublishError("OPT_OUTPUT_EXISTS: output must be a directory")
        if resume:
            return None
        if existing != "overwrite":
            raise ArtifactPublishError("OPT_OUTPUT_EXISTS: use --resume or --existing overwrite")
        backup = output.with_name(f".{output.name}.backup-{uuid.uuid4().hex}")
        output.rename(backup)
        try:
            output.mkdir()
            write_json(output / "overwrite_backup.json", {"name": backup.name})
        except BaseException:
            if output.exists():
                shutil.rmtree(output)
            backup.rename(output)
            raise
        return backup
    if resume:
        raise ValueError("OPT_RESUME_INVALID: experiment does not exist")
    output.mkdir()
    return None


def finish_backup(output: Path) -> None:
    marker = output / "overwrite_backup.json"
    if not marker.exists():
        return
    name = read_json(marker)["name"]
    if (
        not isinstance(name, str)
        or not name.startswith(f".{output.name}.backup-")
        or Path(name).name != name
    ):
        raise ValueError("OPT_RESULT_INVALID: unsafe backup record")
    backup = safe_output(output.parent / name)
    if backup.exists():
        shutil.rmtree(backup)
    marker.unlink()
