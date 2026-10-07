from __future__ import annotations

import gzip
import hashlib
import json
import time
import tomllib
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Event
from typing import Any

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.client import (
    TushareCancelledError,
    TushareError,
)
from xqatexp.providers.tushare.collection import (
    collection_lock,
    latest_entries,
    member_path,
    read_index,
    register_artifact,
    safe_directory,
    valid_entry,
    validate_collection,
    write_json,
)
from xqatexp.providers.tushare.raw import (
    FetchRequest,
    RawFetchService,
    artifact_matches,
    partition_id,
    request_identity,
    request_parameters,
)
from xqatexp.providers.tushare.registry import DAILY_DATASETS, VIP_APIS, get_dataset


@dataclass(frozen=True, slots=True)
class BatchPlan:
    start: date
    end: date
    datasets: tuple[str, ...]
    period_start: date | None = None
    period_end: date | None = None
    requests: tuple[dict[str, Any], ...] = ()
    refresh_days: int = 5
    refresh_periods: int = 8

    @classmethod
    def load(cls, path: Path) -> BatchPlan:
        value = tomllib.loads(path.read_text("utf-8"))
        allowed = {
            "start",
            "end",
            "datasets",
            "period_start",
            "period_end",
            "requests",
            "refresh_days",
            "refresh_periods",
        }
        if set(value) - allowed:
            raise ValueError("CONFIG_SCHEMA_INVALID: unknown batch settings")
        try:
            start, end = _date(value["start"]), _date(value["end"])
            datasets = value["datasets"]
            if (
                not isinstance(datasets, list)
                or not datasets
                or len(datasets) != len(set(datasets))
                or any(dataset not in DAILY_DATASETS | set(VIP_APIS) for dataset in datasets)
            ):
                raise ValueError("datasets")
            periods = (
                _date(value["period_start"]) if "period_start" in value else None,
                _date(value["period_end"]) if "period_end" in value else None,
            )
            if set(datasets) & set(VIP_APIS) and (
                periods[0] is None or periods[1] is None or periods[0] > periods[1]
            ):
                raise ValueError("financial datasets require period_start and period_end")
            if (
                set(datasets) & set(VIP_APIS)
                and periods[0] is not None
                and periods[1] is not None
                and not report_periods(periods[0], periods[1])
            ):
                raise ValueError("financial range contains no quarter ends")
            if start > end:
                raise ValueError("start exceeds end")
            for name in ("refresh_days", "refresh_periods"):
                if name in value and (type(value[name]) is not int or value[name] < 0):
                    raise ValueError(name)
            requests = value.get("requests", [])
            if not isinstance(requests, list):
                raise ValueError("requests")
            for request in requests:
                if (
                    not isinstance(request, dict)
                    or set(request) - {"dataset", "start", "end", "security_id"}
                    or request.get("dataset") in DAILY_DATASETS | set(VIP_APIS)
                ):
                    raise ValueError("explicit request")
                get_dataset(request["dataset"])
                if _date(request.get("start", start)) > _date(request.get("end", end)):
                    raise ValueError("explicit date range")
                if "security_id" in request and not isinstance(request["security_id"], str):
                    raise ValueError("security_id")
                if request.get("security_id") == "*":
                    if request["dataset"] != "dividend":
                        raise ValueError("wildcard is supported only for dividend")
                else:
                    request_parameters(
                        _request(
                            request["dataset"],
                            _date(request.get("start", start)),
                            _date(request.get("end", end)),
                            path.parent,
                            security=request.get("security_id"),
                        )
                    )
            return cls(
                start,
                end,
                tuple(datasets),
                *periods,
                tuple(requests),
                value.get("refresh_days", 5),
                value.get("refresh_periods", 8),
            )
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("CONFIG_SCHEMA_INVALID: invalid batch plan") from error


def _date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError("expected ISO date")


def report_periods(start: date, end: date) -> tuple[date, ...]:
    return tuple(
        period
        for year in range(start.year, end.year + 1)
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))
        if start <= (period := date(year, month, day)) <= end
    )


def _request(
    dataset: str,
    start: date,
    end: date,
    root: Path,
    *,
    security: str | None = None,
    api: str | None = None,
    period: date | None = None,
) -> FetchRequest:
    return FetchRequest(
        dataset,
        start,
        end,
        (security,) if security else (),
        (),
        root,
        OverwritePolicy.ERROR,
        api,
        period,
    )


def trade_dates(path: Path, start: date, end: date) -> tuple[date, ...]:
    with gzip.open(path / "response.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    covered = {_date(_iso(row["cal_date"])) for row in rows if row["exchange"] == "SSE"}
    if any(day not in covered for day in _calendar_days(start, end)):
        raise ValueError("DATA_COVERAGE_INSUFFICIENT: trade calendar has missing days")
    return tuple(
        sorted(
            {
                _date(_iso(row["cal_date"]))
                for row in rows
                if row["exchange"] == "SSE"
                and int(row["is_open"]) == 1
                and start <= _date(_iso(row["cal_date"])) <= end
            }
        )
    )


def _iso(value: str) -> str:
    return value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def _calendar_days(start: date, end: date) -> list[date]:
    return [date.fromordinal(day) for day in range(start.toordinal(), end.toordinal() + 1)]


def plan_requests(
    plan: BatchPlan, dates: tuple[date, ...], root: Path, securities: tuple[str, ...] = ()
) -> list[FetchRequest]:
    jobs = [
        _request(dataset, day, day, root)
        for dataset in plan.datasets
        if dataset in DAILY_DATASETS
        for day in dates
    ]
    if plan.period_start is not None and plan.period_end is not None:
        jobs.extend(
            _request(dataset, period, period, root, api=VIP_APIS[dataset], period=period)
            for dataset in plan.datasets
            if dataset in VIP_APIS
            for period in report_periods(plan.period_start, plan.period_end)
        )
    for item in plan.requests:
        dataset = item["dataset"]
        start, end = _date(item.get("start", plan.start)), _date(item.get("end", plan.end))
        security = item.get("security_id")
        if security == "*":
            if not securities:
                raise ValueError("DATA_REQUIRED_MISSING: wildcard requires stock_basic")
            jobs.extend(_request(dataset, start, end, root, security=code) for code in securities)
        elif dataset in {"fund_daily", "fund_adj_factor", "index_daily"}:
            if start < plan.start or end > plan.end:
                raise ValueError(
                    "CONFIG_VALUE_INVALID: daily explicit requests must fit calendar range"
                )
            jobs.extend(
                _request(dataset, day, day, root, security=security)
                for day in dates
                if start <= day <= end
            )
        elif dataset == "trade_calendar":
            raise ValueError("CONFIG_VALUE_INVALID: trade_calendar is an automatic dependency")
        else:
            jobs.append(_request(dataset, start, end, root, security=security))
    if len({partition_id(job) for job in jobs}) != len(jobs):
        raise ValueError("CONFIG_VALUE_INVALID: duplicate batch partitions")
    return jobs


def dry_run(plan: BatchPlan, root: Path) -> dict[str, Any]:
    # Offline plans use verified calendars; otherwise expose the unresolved dependency.
    dates: tuple[date, ...] | None = None
    if root.is_dir():
        value = read_index(safe_directory(root))
        for entry in reversed(value["entries"]):
            identity = entry["identity"]
            if (
                identity["dataset_id"] == "trade_calendar"
                and identity["start"] <= plan.start.isoformat()
                and identity["end"] >= plan.end.isoformat()
                and valid_entry(root, entry)
            ):
                dates = trade_dates(member_path(root, entry["path"]), plan.start, plan.end)
                break
    result: dict[str, Any] = {
        "network_requests": 0,
        "calendar_required": dates is None,
        "start": plan.start.isoformat(),
        "end": plan.end.isoformat(),
    }
    if dates is not None:
        securities = (
            _stock_ids(root, value)
            if any(item.get("security_id") == "*" for item in plan.requests)
            else ()
        )
        if any(item.get("security_id") == "*" for item in plan.requests) and not securities:
            result.update(stock_basic_required=True, partition_count=None)
            return result
        jobs = plan_requests(plan, dates, root, securities)
        result.update(
            partitions=[request_identity(job) for job in jobs],
            partition_count=len(jobs),
            request_estimate_minimum=2 * len(jobs),
        )
    else:
        result.update(
            trade_day_partition_count=None,
            datasets=list(plan.datasets),
            financial_periods=[
                day.isoformat() for day in report_periods(plan.period_start, plan.period_end)
            ]
            if plan.period_start is not None and plan.period_end is not None
            else [],
            note=("Fetch calendar first; HTTP count depends on pagination and retries."),
        )
    return result


class BatchRunner:
    def __init__(
        self,
        client: Any,
        *,
        workers: int = 4,
        cancelled: Event | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> None:
        if not 1 <= workers <= 8:
            raise ValueError("CONFIG_VALUE_INVALID: workers must be in [1, 8]")
        self.client, self.workers = client, workers
        self.cancelled = cancelled or Event()
        self.progress = progress
        self.configuration = configuration or {"workers": workers}
        self._blocked_apis: set[str] = set()
        self._last_checkpoint = 0.0

    def run(self, plan: BatchPlan, root: Path, *, mode: str = "bootstrap") -> dict[str, Any]:
        if mode not in ("bootstrap", "update"):
            raise ValueError("CONFIG_VALUE_INVALID: unknown batch mode")
        root = safe_directory(root)
        metrics = getattr(self.client, "metrics", None)
        self._initial_metrics = metrics() if callable(metrics) else {}
        self._started = time.monotonic()
        self._blocked_apis.clear()
        self._last_checkpoint = 0.0
        with collection_lock(root):
            value = read_index(root)
            self._latest = latest_entries(value)
            self._indexed_paths = {entry["path"] for entry in value["entries"]}
            self._generations: dict[str, list[dict[str, Any]]] = {}
            for entry in value["entries"]:
                self._generations.setdefault(entry["partition_id"], []).append(entry)
            fingerprint = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "start": plan.start,
                        "end": plan.end,
                        "datasets": plan.datasets,
                        "period_start": plan.period_start,
                        "period_end": plan.period_end,
                        "requests": plan.requests,
                        "refresh_days": plan.refresh_days,
                        "refresh_periods": plan.refresh_periods,
                        "mode": mode,
                    }
                )
            ).hexdigest()
            resumed: set[str] = set()
            previous_report = member_path(root, "batch-result.json")
            if previous_report.is_file():
                previous_value = json.loads(previous_report.read_text("utf-8"))
                SchemaRegistry().validate_json("batch_result", previous_value)
                if (
                    not previous_value["complete"]
                    and previous_value.get("plan_fingerprint") == fingerprint
                ):
                    resumed = {
                        task["partition_id"]
                        for task in previous_value["tasks"]
                        if task["status"] in ("succeeded", "skipped")
                    }
                    for entry in value["entries"]:
                        path = member_path(root, entry["path"])
                        if path.is_dir() and valid_entry(root, entry):
                            created = json.loads((path / "manifest.json").read_text("utf-8"))[
                                "created_at"
                            ]
                            if created >= previous_value["started_at"]:
                                resumed.add(entry["partition_id"])
            value["complete"] = False
            report: dict[str, Any] = {
                "schema_version": "1.0",
                "run_id": str(uuid.uuid4()),
                "mode": mode,
                "plan_fingerprint": fingerprint,
                "started_at": _now(),
                "completed_at": None,
                "complete": False,
                "tasks": [],
                "metrics": {},
                "runtime": self.configuration,
            }
            self._checkpoint(root, value, report)
            calendar = _request("trade_calendar", plan.start, plan.end, root)
            jobs: list[FetchRequest] = []
            try:
                task = self._obtain(
                    calendar,
                    root,
                    value,
                    refresh=mode == "update" and partition_id(calendar) not in resumed,
                )
                report["tasks"].append(task)
                self._checkpoint(root, value, report)
                if task["status"] not in ("succeeded", "skipped"):
                    raise ValueError("DATA_COVERAGE_INSUFFICIENT: calendar acquisition failed")
                dates = trade_dates(member_path(root, task["path"]), plan.start, plan.end)
                dependencies = [calendar]
                planned = plan
                securities: tuple[str, ...] = ()
                if any(item.get("security_id") == "*" for item in plan.requests):
                    basic = next(
                        (item for item in plan.requests if item["dataset"] == "stock_basic"), {}
                    )
                    master = _request(
                        "stock_basic",
                        _date(basic.get("start", plan.start)),
                        _date(basic.get("end", plan.end)),
                        root,
                    )
                    task = self._obtain(
                        master,
                        root,
                        value,
                        refresh=mode == "update" and partition_id(master) not in resumed,
                    )
                    report["tasks"].append(task)
                    self._checkpoint(root, value, report)
                    if task["status"] not in ("succeeded", "skipped"):
                        raise ValueError("DATA_REQUIRED_MISSING: stock_basic acquisition failed")
                    dependencies.append(master)
                    securities = _stock_ids(root, value)
                    planned = replace(
                        plan,
                        requests=tuple(
                            item for item in plan.requests if item["dataset"] != "stock_basic"
                        ),
                    )
                jobs = plan_requests(planned, dates, root, securities)
                expected = set(value["expected_partitions"])
                expected.update(partition_id(job) for job in [*dependencies, *jobs])
                value["expected_partitions"] = sorted(expected)
                periods = (
                    report_periods(plan.period_start, plan.period_end)
                    if (plan.period_start is not None and plan.period_end is not None)
                    else ()
                )
                refresh_days = set(dates[-plan.refresh_days :]) if plan.refresh_days else set()
                refresh_periods = (
                    set(periods[-plan.refresh_periods :]) if plan.refresh_periods else set()
                )
                self._checkpoint(root, value, report)
                pending: dict[Future[dict[str, Any]], FetchRequest] = {}
                iterator = iter(jobs)
                pool = ThreadPoolExecutor(max_workers=self.workers)
                try:
                    exhausted = False
                    while pending or not exhausted:
                        while (
                            not exhausted
                            and not self.cancelled.is_set()
                            and len(pending) < self.workers
                        ):
                            job = next(iterator, None)
                            if job is None:
                                exhausted = True
                                break
                            api = job.api_name or get_dataset(job.dataset_id).api_name
                            if api in self._blocked_apis:
                                report["tasks"].append(
                                    self._task(job, "not_executed", "API_BLOCKED")
                                )
                                continue
                            refreshed = mode == "update" and (
                                job.period in refresh_periods
                                if job.period is not None
                                else job.start_date in refresh_days
                                or (
                                    job.dataset_id not in DAILY_DATASETS
                                    and job.dataset_id
                                    not in {"fund_daily", "fund_adj_factor", "index_daily"}
                                )
                            )
                            # Resume lookup, validation, and index mutation stay on the coordinator.
                            if partition_id(job) in resumed:
                                refreshed = False
                            previous = self._latest.get(partition_id(job))
                            if (
                                previous is not None
                                and not refreshed
                                and valid_entry(root, previous)
                            ):
                                report["tasks"].append(
                                    self._task(job, "skipped", path=previous["path"])
                                )
                                self._checkpoint(root, value, report)
                                continue
                            recovery = self._recover(job, root, value)
                            if recovery is not None:
                                report["tasks"].append(recovery)
                                continue
                            output = root / "artifacts" / partition_id(job) / uuid.uuid4().hex
                            pending[pool.submit(self._fetch, replace(job, output_path=output))] = (
                                job
                            )
                        if self.cancelled.is_set():
                            exhausted = True
                        if not pending:
                            continue
                        done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                        for future in done:
                            job = pending.pop(future)
                            task = future.result()
                            if task["status"] == "succeeded":
                                self._register(root, value, member_path(root, task["path"]))
                            if task.get("error_category") in (
                                "TusharePermissionError",
                                "TushareSchemaError",
                            ) or "DATA_PROVIDER_SCHEMA_MISMATCH" in str(task.get("error_code")):
                                self._blocked_apis.add(task["identity"]["api_name"])
                            if task.get("error_category") == "TushareAuthenticationError":
                                self.cancelled.set()
                            report["tasks"].append(task)
                        self._checkpoint(root, value, report)
                except KeyboardInterrupt:
                    self.cancelled.set()
                    for future in pending:
                        task = future.result()
                        if task["status"] == "succeeded":
                            self._register(root, value, member_path(root, task["path"]))
                        report["tasks"].append(task)
                    raise
                finally:
                    pool.shutdown(wait=True, cancel_futures=True)
            except KeyboardInterrupt:
                self.cancelled.set()
            except (ValueError, OSError, TushareError) as error:
                report["tasks"].append(self._task(calendar, "failed", str(error).split(":", 1)[0]))
            finally:
                completed = {task["partition_id"] for task in report["tasks"]}
                for job in jobs:
                    if partition_id(job) not in completed:
                        report["tasks"].append(
                            self._task(job, "interrupted", "DATA_FETCH_CANCELLED")
                        )
                report["completed_at"] = _now()
                report["complete"] = bool(report["tasks"]) and all(
                    task["status"] in ("succeeded", "skipped") for task in report["tasks"]
                )
                value["complete"] = report["complete"]
                if value["complete"]:
                    try:
                        validate_collection(root, value)
                    except (OSError, ValueError) as error:
                        value["complete"] = report["complete"] = False
                        report["tasks"].append(
                            self._task(calendar, "failed", str(error).split(":", 1)[0])
                        )
                self._checkpoint(root, value, report, force=True)
            return report

    def _register(self, root: Path, value: dict[str, Any], path: Path) -> None:
        identifier = path.parent.name
        previous = self._generations.get(identifier, [])
        entry = register_artifact(root, value, path, previous)
        self._latest[entry["partition_id"]] = entry
        self._indexed_paths.add(entry["path"])
        self._generations.setdefault(entry["partition_id"], []).append(entry)

    def _recover(
        self, job: FetchRequest, root: Path, value: dict[str, Any]
    ) -> dict[str, Any] | None:
        parent = root / "artifacts" / partition_id(job)
        if parent.is_dir():
            for path in sorted(parent.iterdir()):
                if (
                    not path.name.startswith(".")
                    and path.relative_to(root).as_posix() not in self._indexed_paths
                    and artifact_matches(job, path)
                ):
                    self._register(root, value, path)
                    return self._task(job, "succeeded", path=path.relative_to(root).as_posix())
        return None

    def _obtain(
        self, job: FetchRequest, root: Path, value: dict[str, Any], *, refresh: bool
    ) -> dict[str, Any]:
        previous = self._latest.get(partition_id(job))
        if previous is not None and not refresh and valid_entry(root, previous):
            return self._task(job, "skipped", path=previous["path"])
        recovered = self._recover(job, root, value)
        if recovered is not None:
            return recovered
        output = root / "artifacts" / partition_id(job) / uuid.uuid4().hex
        task = self._fetch(replace(job, output_path=output))
        if task["status"] == "succeeded":
            self._register(root, value, member_path(root, task["path"]))
        return task

    def _fetch(self, job: FetchRequest) -> dict[str, Any]:
        started = time.monotonic()
        task = self._task(job, "succeeded")
        task["started_at"] = _now()
        try:
            if self.cancelled.is_set():
                raise TushareCancelledError("DATA_FETCH_CANCELLED: runtime cancelled")
            RawFetchService(self.client).fetch(job)
            request = json.loads((job.output_path / "request.json").read_text("utf-8"))
            task.update(
                attempts=request["attempt_count"],
                row_count=request["response_row_count"],
                path=job.output_path.relative_to(job.output_path.parents[2]).as_posix(),
            )
            # output is <collection>/artifacts/<partition>/<generation>.
        except (TushareError, ValueError, OSError, RuntimeError) as error:
            task.update(
                status="interrupted" if isinstance(error, TushareCancelledError) else "failed",
                error_code=(
                    "DATA_PROVIDER_SCHEMA_MISMATCH"
                    if "DATA_PROVIDER_SCHEMA_MISMATCH" in str(error)
                    else str(error).split(":", 1)[0]
                ),
                error_category=type(error).__name__,
                attempts=getattr(error, "attempts", 0),
                http_status=getattr(error, "http_status", None),
                provider_code=getattr(error, "provider_code", None),
            )
        task.update(completed_at=_now(), elapsed_seconds=time.monotonic() - started)
        return task

    def _task(
        self, job: FetchRequest, status: str, error: str | None = None, *, path: str | None = None
    ) -> dict[str, Any]:
        return {
            "partition_id": partition_id(job),
            "identity": request_identity(job),
            "status": status,
            "path": path,
            "error_code": error,
            "error_category": None,
            "attempts": 0,
            "row_count": None,
            "http_status": None,
            "provider_code": None,
            "started_at": None,
            "completed_at": None,
            "elapsed_seconds": 0.0,
        }

    def _checkpoint(
        self, root: Path, value: dict[str, Any], report: dict[str, Any], *, force: bool = False
    ) -> None:
        if not force and time.monotonic() - self._last_checkpoint < 2:
            return
        self._last_checkpoint = time.monotonic()
        metrics = getattr(self.client, "metrics", None)
        report["metrics"] = (
            {
                name: number - self._initial_metrics.get(name, 0)
                for name, number in metrics().items()
            }
            if callable(metrics)
            else {}
        )
        elapsed = time.monotonic() - self._started
        report["metrics"]["elapsed_seconds"] = elapsed
        report["metrics"]["effective_calls_per_minute"] = (
            report["metrics"].get("requests", 0) * 60 / elapsed if elapsed else 0
        )
        write_json(root / "collection.json", value, "raw_collection")
        write_json(root / "batch-result.json", report, "batch_result")
        if self.progress is not None:
            self.progress(report)


def _stock_ids(root: Path, value: dict[str, Any]) -> tuple[str, ...]:
    for entry in reversed(value["entries"]):
        if entry["identity"]["dataset_id"] == "stock_basic" and valid_entry(root, entry):
            with gzip.open(
                member_path(root, entry["path"]) / "response.jsonl.gz", "rt", encoding="utf-8"
            ) as stream:
                return tuple(sorted({json.loads(line)["ts_code"] for line in stream}))
    return ()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
