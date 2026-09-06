from __future__ import annotations

import gzip
import hashlib
import io
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from xqatexp import __version__
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublisher, PublishedArtifact
from xqatexp.artifacts.readers import ArtifactReader, ArtifactReadError
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.client import QueryResult
from xqatexp.providers.tushare.registry import get_dataset, get_dataset_by_api


class QueryClient(Protocol):
    def query(
        self, api_name: str, fields: Sequence[str], params: dict[str, object]
    ) -> QueryResult: ...


@dataclass(frozen=True, slots=True)
class FetchRequest:
    dataset_id: str
    start_date: date
    end_date: date
    security_ids: tuple[str, ...]
    fields: tuple[str, ...]
    output_path: Path
    existing_policy: OverwritePolicy


class RawFetchService:
    def __init__(
        self,
        client: QueryClient,
        *,
        publisher: ArtifactPublisher | None = None,
        schemas: SchemaRegistry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._publisher = publisher or ArtifactPublisher()
        self._schemas = schemas or SchemaRegistry()
        self._clock = clock

    def fetch(self, request: FetchRequest) -> PublishedArtifact:
        if request.start_date != request.end_date:
            raise ValueError("CONFIG_VALUE_INVALID: MVP fetch request must be one explicit slice")
        spec = get_dataset(request.dataset_id)
        fields = request.fields or spec.fields
        if not set(spec.fields).issubset(fields):
            raise ValueError("CONFIG_VALUE_INVALID: requested fields omit registered fields")
        params: dict[str, object] = dict(spec.fixed_params)
        if spec.dataset_id in {
            "stock_daily",
            "stock_adj_factor",
            "stock_daily_basic",
            "stock_suspend",
            "stock_price_limit",
            "stock_st_status",
        }:
            params["trade_date"] = request.start_date.strftime("%Y%m%d")
        result = self._client.query(spec.api_name, fields, params)
        if not result.records and not spec.empty_allowed:
            raise ValueError(f"DATA_PROVIDER_EMPTY_RESPONSE: {request.dataset_id}")
        now = self._clock()
        timestamp = now.isoformat().replace("+00:00", "Z")
        request_id = str(uuid.uuid4())

        def build(staging: Path) -> None:
            request_value = {
                "schema_version": "1.0",
                "request_id": request_id,
                "provider": "tushare",
                "api_name": spec.api_name,
                "requested_fields": list(fields),
                "parameters": params,
                "page_number": 1,
                "offset": 0,
                "limit": max(1, len(result.records)),
                "started_at": timestamp,
                "completed_at": timestamp,
                "attempt_count": result.attempts,
                "response_row_count": len(result.records),
            }
            self._schemas.validate_json("raw_request", request_value)
            request_bytes = canonical_json_bytes(request_value)
            response_bytes = self._response_bytes(result.records, request_id)
            (staging / "request.json").write_bytes(request_bytes)
            (staging / "response.jsonl.gz").write_bytes(response_bytes)
            files = [
                self._file_entry(
                    "request.json", request_bytes, "application/json", "raw_request", 1
                ),
                self._file_entry(
                    "response.jsonl.gz",
                    response_bytes,
                    "application/gzip",
                    "raw_response",
                    len(result.records),
                ),
            ]
            manifest = {
                "schema_version": "1.0",
                "artifact_type": "RAW_DATA",
                "artifact_id": str(uuid.uuid4()),
                "created_at": now,
                "producer": {"name": "xqatexp", "version": __version__},
                "run": None,
                "inputs": [],
                "date_scope": {
                    "start": request.start_date,
                    "end": request.end_date,
                },
                "files": sorted(files, key=lambda entry: str(entry["path"])),
                "issues": [],
                "limitations": [],
            }
            (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

        return self._publisher.publish(build, request.output_path, request.existing_policy)

    @staticmethod
    def _response_bytes(records: tuple[dict[str, object], ...], request_id: str) -> bytes:
        raw = io.BytesIO()
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            for row_number, original in enumerate(records, start=1):
                record = dict(original)
                reserved = {"_xqat_request_id", "_xqat_page_number", "_xqat_row_number"}
                if reserved.intersection(record):
                    raise ValueError("DATA_PROVIDER_SCHEMA_MISMATCH: reserved Raw field collision")
                record["_xqat_request_id"] = request_id
                record["_xqat_page_number"] = 1
                record["_xqat_row_number"] = row_number
                line = json.dumps(
                    record,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                compressed.write(line + b"\n")
        return raw.getvalue()

    @staticmethod
    def _file_entry(
        path: str, payload: bytes, media_type: str, schema_id: str, row_count: int
    ) -> dict[str, object]:
        return {
            "path": path,
            "media_type": media_type,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "row_count": row_count,
            "schema_id": schema_id,
            "schema_version": "1.0",
        }


@dataclass(frozen=True, slots=True)
class RawCheckReport:
    valid: bool
    row_count: int
    issue_codes: tuple[str, ...]


class RawCheckService:
    """Validate one immutable Raw artifact without changing it."""

    def __init__(self, reader: ArtifactReader | None = None) -> None:
        self._reader = reader or ArtifactReader()

    def check(self, path: Path) -> RawCheckReport:
        try:
            opened = self._reader.open(path)
            request = json.loads((opened.path / "request.json").read_text(encoding="utf-8"))
            spec = get_dataset_by_api(str(request["api_name"]))
            requested_fields = tuple(str(field) for field in request["requested_fields"])
            issues: set[str] = set()
            if not set(spec.fields).issubset(requested_fields):
                issues.add("DATA_REQUIRED_MISSING")
            records = self._read_records(opened.path / "response.jsonl.gz", issues)
            seen: set[tuple[object, ...]] = set()
            for record in records:
                if not set(spec.fields).issubset(record):
                    issues.add("DATA_REQUIRED_MISSING")
                    continue
                key = tuple(record[field] for field in spec.business_key)
                if key in seen:
                    issues.add("DATA_CONFLICT")
                seen.add(key)
            ordered = tuple(
                code
                for code in ("DATA_INPUT_CORRUPT", "DATA_REQUIRED_MISSING", "DATA_CONFLICT")
                if code in issues
            )
            return RawCheckReport(not ordered, len(records), ordered)
        except (
            ArtifactReadError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ):
            return RawCheckReport(False, 0, ("DATA_INPUT_CORRUPT",))

    @staticmethod
    def _read_records(path: Path, issues: set[str]) -> tuple[dict[str, object], ...]:
        records = []
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        issues.add("DATA_INPUT_CORRUPT")
                        continue
                    records.append(value)
        except (OSError, UnicodeError, json.JSONDecodeError):
            issues.add("DATA_INPUT_CORRUPT")
        return tuple(records)
