from __future__ import annotations

import gzip
import hashlib
import importlib
import json
import os
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.providers.tushare.raw import RawCheckService
from xqatexp.providers.tushare.registry import VIP_APIS, get_dataset_by_api

FINANCIAL_DATASETS = frozenset(VIP_APIS)


def safe_directory(path: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    for component in (lexical, *lexical.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ValueError("ARTIFACT_UNSAFE_OUTPUT_PATH: collection symlink or junction")
    if lexical == Path(lexical.anchor) or lexical == Path.cwd():
        raise ValueError("ARTIFACT_UNSAFE_OUTPUT_PATH: collection root is unsafe")
    return lexical


def member_path(root: Path, relative: str) -> Path:
    name = PurePosixPath(relative)
    if (
        name.is_absolute()
        or not name.parts
        or any(part in ("..", ".") for part in name.parts)
        or "\\" in relative
    ):
        raise ValueError("DATA_INPUT_CORRUPT: collection path escapes root")
    path = root.joinpath(*name.parts)
    for component in (path, *path.parents):
        if component == root.parent:
            break
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ValueError("DATA_INPUT_CORRUPT: collection symlink or junction")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("DATA_INPUT_CORRUPT: collection path escapes root")
    return path


@contextmanager
def collection_lock(root: Path) -> Iterator[None]:
    root = safe_directory(root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".collection.lock"
    if lock_path.is_symlink():
        raise ValueError("ARTIFACT_UNSAFE_OUTPUT_PATH: symlinked collection lock")
    with lock_path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                msvcrt: Any = importlib.import_module("msvcrt")

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("DATA_COLLECTION_BUSY: another writer owns collection") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def write_json(path: Path, value: dict[str, Any], schema_id: str) -> None:
    SchemaRegistry().validate_json(schema_id, value)
    safe_directory(path.parent)
    if path.is_symlink():
        raise ValueError("ARTIFACT_UNSAFE_OUTPUT_PATH: symlinked index/report")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def empty_collection() -> dict[str, Any]:
    return {"schema_version": "1.0", "complete": False, "expected_partitions": [], "entries": []}


def read_index(root: Path) -> dict[str, Any]:
    path = member_path(root, "collection.json")
    if not path.exists():
        return empty_collection()
    value: dict[str, Any] = json.loads(path.read_text("utf-8"))
    SchemaRegistry().validate_json("raw_collection", value)
    identifiers = [(entry["partition_id"], entry["generation"]) for entry in value["entries"]]
    if len(identifiers) != len(set(identifiers)) or len(
        {entry["path"] for entry in value["entries"]}
    ) != len(value["entries"]):
        raise ValueError("DATA_INPUT_CORRUPT: duplicate collection generations")
    return value


def describe_artifact(root: Path, path: Path, *, generation: int = 1) -> dict[str, Any]:
    path = member_path(root, path.relative_to(root).as_posix())
    for name in ("manifest.json", "request.json", "response.jsonl.gz"):
        member_path(root, (path / name).relative_to(root).as_posix())
    if not RawCheckService().check(path).valid:
        raise ValueError(f"DATA_INPUT_CORRUPT: invalid Raw artifact {path.name}")
    opened = ArtifactReader().open(path)
    request = json.loads((path / "request.json").read_text("utf-8"))
    spec = get_dataset_by_api(request["api_name"])
    identity = {
        "dataset_id": spec.dataset_id,
        "api_name": request["api_name"],
        "fields": request["requested_fields"],
        "parameters": request["parameters"],
        "start": opened.manifest["date_scope"]["start"],
        "end": opened.manifest["date_scope"]["end"],
    }
    digest = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    return {
        "partition_id": digest,
        "generation": generation,
        "path": path.relative_to(root).as_posix(),
        "identity": identity,
        "manifest_sha256": hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest(),
    }


def valid_entry(root: Path, entry: dict[str, Any]) -> bool:
    # Path violations are permanent, not a damaged partition eligible for replacement.
    path = member_path(root, entry["path"])
    try:
        actual = describe_artifact(root, path, generation=entry["generation"])
        return actual == entry
    except (OSError, ValueError, KeyError):
        return False


def latest_entries(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for entry in value["entries"]:
        old = latest.get(entry["partition_id"])
        if old is None or entry["generation"] > old["generation"]:
            latest[entry["partition_id"]] = entry
    return latest


def register_artifact(
    root: Path,
    value: dict[str, Any],
    path: Path,
    previous_entries: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entry = describe_artifact(root, path)
    existing = (
        list(previous_entries)
        if previous_entries is not None
        else [item for item in value["entries"] if item["partition_id"] == entry["partition_id"]]
    )
    entry["generation"] = max((item["generation"] for item in existing), default=0) + 1
    invalid = {id(item) for item in existing if not valid_entry(root, item)}
    if invalid:
        value["entries"] = [item for item in value["entries"] if id(item) not in invalid]
    value["entries"].append(entry)
    return entry


def _discover(root: Path) -> list[Path]:
    artifacts: list[Path] = []
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = sorted(name for name in children if not name.startswith("."))
        for name in children:
            member_path(root, (Path(directory) / name).relative_to(root).as_posix())
        if "manifest.json" in files:
            artifacts.append(Path(directory))
            children[:] = []
    return sorted(artifacts)


def index_collection(root: Path) -> dict[str, Any]:
    root = safe_directory(root)
    with collection_lock(root):
        value = read_index(root)
        indexed_paths = {entry["path"] for entry in value["entries"]}
        for path in _discover(root):
            if path.relative_to(root).as_posix() not in indexed_paths:
                entry = describe_artifact(root, path)
                old = latest_entries(value).get(entry["partition_id"])
                if entry["identity"]["dataset_id"] in {
                    "trade_calendar",
                    "stock_basic",
                    "fund_basic",
                }:
                    for previous in value["entries"]:
                        other = previous["identity"]
                        identity = entry["identity"]
                        if (
                            other["dataset_id"] == identity["dataset_id"]
                            and other["start"] <= identity["end"]
                            and identity["start"] <= other["end"]
                        ):
                            raise ValueError("DATA_CONFLICT: ambiguous imported snapshots")
                if old is not None:
                    raise ValueError("DATA_CONFLICT: unindexed overlapping Raw partition")
                value["entries"].append(entry)
        if not value["entries"]:
            raise ValueError("DATA_REQUIRED_MISSING: no Raw artifacts found")
        if not value["expected_partitions"]:
            value["expected_partitions"] = sorted(latest_entries(value))
            value["complete"] = True
        # Never erase a failed batch's declared coverage by re-indexing its partial directory.
        validate_collection(root, value)
        write_json(root / "collection.json", value, "raw_collection")
        return value


def validate_collection(root: Path, value: dict[str, Any]) -> tuple[Path, ...]:
    if not value["complete"]:
        raise ValueError("DATA_COVERAGE_INSUFFICIENT: collection batch is incomplete")
    latest = latest_entries(value)
    if not value["expected_partitions"] or not latest:
        raise ValueError("DATA_REQUIRED_MISSING: collection has no declared coverage")
    if not set(value["expected_partitions"]).issubset(latest):
        raise ValueError("DATA_COVERAGE_INSUFFICIENT: collection has missing partitions")
    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in value["entries"]:
        identity = entry["identity"]
        if identity["dataset_id"] in {"stock_basic", "fund_basic", "dividend"}:
            snapshots[(identity["dataset_id"], str(identity["parameters"].get("ts_code", "*")))] = (
                entry
            )
    selected = [
        entry
        for entry in value["entries"]
        if entry["identity"]["dataset_id"] in FINANCIAL_DATASETS
        or entry is latest[entry["partition_id"]]
    ]
    selected = [
        entry
        for entry in selected
        if entry["identity"]["dataset_id"] not in {"stock_basic", "fund_basic", "dividend"}
        or entry
        is snapshots[
            (
                entry["identity"]["dataset_id"],
                str(entry["identity"]["parameters"].get("ts_code", "*")),
            )
        ]
    ]
    calendars: list[dict[str, Any]] = []
    for entry in selected:
        identity = entry["identity"]
        if identity["dataset_id"] == "trade_calendar":
            calendars = [
                old
                for old in calendars
                if not (
                    identity["start"] <= old["identity"]["start"]
                    and identity["end"] >= old["identity"]["end"]
                )
            ]
            calendars.append(entry)
    selected = [
        entry
        for entry in selected
        if entry["identity"]["dataset_id"] != "trade_calendar"
        or any(entry is calendar for calendar in calendars)
    ]
    for entry in selected:
        if not valid_entry(root, entry):
            raise ValueError("DATA_INPUT_CORRUPT: collection member changed or is invalid")
    financial: dict[str, list[dict[str, Any]]] = {}
    for entry in selected:
        dataset = entry["identity"]["dataset_id"]
        if dataset in FINANCIAL_DATASETS:
            with gzip.open(
                member_path(root, entry["path"]) / "response.jsonl.gz", "rt", encoding="utf-8"
            ) as stream:
                financial.setdefault(dataset, []).extend(json.loads(line) for line in stream)
    for dataset, records in financial.items():
        merge_financial_records(records, get_dataset_by_api(dataset).business_key)
    # Validate interval overlaps in O(n log n), including wildcard vs single-security scopes.
    ordinary: dict[str, list[tuple[str, str, str]]] = {}
    for entry in selected:
        identity = entry["identity"]
        if identity["dataset_id"] in FINANCIAL_DATASETS | {"trade_calendar"}:
            continue
        ordinary.setdefault(identity["dataset_id"], []).append(
            (identity["start"], identity["end"], str(identity["parameters"].get("ts_code", "*")))
        )
    for intervals in ordinary.values():
        ends: dict[str, str] = {}
        for start, end, scope in sorted(intervals):
            candidates = ends.values() if scope == "*" else (ends.get("*", ""), ends.get(scope, ""))
            if any(previous >= start for previous in candidates):
                raise ValueError("DATA_CONFLICT: overlapping collection ranges")
            ends[scope] = max(end, ends.get(scope, ""))
    return tuple(member_path(root, entry["path"]) for entry in selected)


def collection_roots(path: Path) -> tuple[Path, ...]:
    root = safe_directory(path if path.is_dir() else path.parent)
    if not path.is_dir() and path.name != "collection.json":
        raise ValueError("CONFIG_VALUE_INVALID: expected collection.json or collection directory")
    value = read_index(root)
    return validate_collection(root, value)


def merge_financial_records(
    records: Sequence[dict[str, Any]], business_key: Sequence[str]
) -> list[dict[str, Any]]:
    unique: dict[tuple[object, ...], dict[str, Any]] = {}
    for record in records:
        key = tuple(record[field] for field in business_key)
        old = unique.get(key)
        if old is not None:
            facts = {
                field: value for field, value in record.items() if not field.startswith("_xqat_")
            }
            previous = {
                field: value for field, value in old.items() if not field.startswith("_xqat_")
            }
            if facts != previous:
                raise ValueError(
                    "DATA_CONFLICT: financial business key changed without a new version"
                )
        else:
            unique[key] = record
    return list(unique.values())
