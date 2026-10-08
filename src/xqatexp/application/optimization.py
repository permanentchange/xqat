from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq

from xqatexp.application.workflows import StrategyWorkflowService
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.config import resolve_config
from xqatexp.domain.contracts import CustomFactorInput, ResolvedRunContext
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.optimization.models import StudySpec, TrialResult, digest
from xqatexp.optimization.reporting import write_report
from xqatexp.optimization.runner import TrialJob, run_trials
from xqatexp.optimization.storage import (
    atomic_write,
    finish_backup,
    prepare_output,
    read_json,
    safe_output,
    study_lock,
    write_json,
)
from xqatexp.reporting.structured import resolved_context_value
from xqatexp.research.tables import ResearchCheckService
from xqatexp.security import validate_disjoint_paths
from xqatexp.strategy.registry import resolve_strategy_spec


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_fingerprint() -> str:
    package = Path(__file__).resolve().parents[1]
    source_schemas = package.parents[1] / "schemas"
    schema_root = source_schemas if source_schemas.is_dir() else package.parent / "schemas"
    return digest(
        {
            **{
                f"src/{path.relative_to(package)}": file_sha(path) for path in package.rglob("*.py")
            },
            **{
                f"schemas/{path.name}": hashlib.sha256(path.read_bytes()).hexdigest()
                for path in schema_root.glob("*.json")
            },
        }
    )


def result_from_value(value: Mapping[str, Any]) -> TrialResult:
    return TrialResult(
        value["id"],
        value["parameters"],
        value["metrics"],
        value["status"],
        tuple(value.get("reasons", [])),
        value.get("artifact_sha256"),
    )


@dataclass(frozen=True)
class BacktestEvaluator:
    context: ResolvedRunContext

    def context_for(self, job: TrialJob) -> ResolvedRunContext:
        spec = resolve_strategy_spec(self.context.strategy_id, self.context.strategy_version)
        return replace(
            self.context,
            parameters=spec.normalize_parameters(job.parameters, {}),
            output_path=job.output,
            run_id=str(uuid.uuid4()),
            generated_at=datetime.now(UTC),
            analysis_periods=(),
        )

    def recover(self, job: TrialJob) -> TrialResult | None:
        if not job.output.exists():
            return None
        safe_output(job.output)
        ArtifactReader().open(job.output)
        actual = read_json(job.output / "resolved_config.json")
        expected = resolved_context_value(self.context_for(job))
        if digest(actual) != digest(expected):
            raise ValueError(f"OPT_RESULT_INVALID: trial input mismatch for {job.id}")
        metrics = read_json(job.output / "metrics.json")
        trades = pq.read_table(job.output / "trades.parquet", columns=["filled_quantity"])
        metrics["trade_count"] = sum(value > 0 for value in trades["filled_quantity"].to_pylist())
        return TrialResult(
            job.id, job.parameters, metrics, "succeeded", (), file_sha(job.output / "manifest.json")
        )

    def __call__(self, job: TrialJob) -> TrialResult:
        if job.output.exists():
            raise ValueError("OPT_RESULT_INVALID: refusing to overwrite a trial")
        StrategyWorkflowService().backtest(self.context_for(job), OverwritePolicy.ERROR)
        return cast(TrialResult, self.recover(job))


def toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise ValueError(f"OPT_CONFIG_INVALID: unsupported TOML value {type(value).__name__}")


def best_config_bytes(
    context: ResolvedRunContext,
    parameters: Mapping[str, object],
    output: Path,
) -> bytes:
    execution = dict(context.execution_assumptions)
    if execution.get("dividend_tax_model") != "FLAT_RATE":
        execution.pop("dividend_tax_rate", None)
    top: dict[str, object] = {
        "schema_version": "1.0",
        "mode": "BACKTEST",
        "strategy_id": context.strategy_id,
        "strategy_version": context.strategy_version,
        "research_artifact": context.research_artifact_path.as_posix(),
        "start_date": str(context.start_date),
        "end_date": str(context.end_date),
        "output": output.as_posix(),
    }
    if context.custom_factor_inputs:
        top["custom_factors"] = [item.path.as_posix() for item in context.custom_factor_inputs]
    lines = [f"{json.dumps(key)} = {toml_value(value)}" for key, value in top.items()]
    for name, values in (("strategy", parameters), ("execution", execution)):
        lines.extend(["", f"[{name}]"])
        lines.extend(
            f"{json.dumps(key)} = {toml_value(value)}" for key, value in sorted(values.items())
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def publish_study_report(
    output: Path,
    spec: StudySpec,
    results: list[TrialResult],
    total: int,
    context: ResolvedRunContext,
    baseline: TrialResult | None = None,
    *,
    notes: Sequence[str] = (),
    artifact_paths: Mapping[str, str] | None = None,
) -> TrialResult | None:
    best = write_report(
        output, spec, results, total, baseline, notes=notes, artifact_paths=artifact_paths
    )
    if best is not None:
        registry = resolve_strategy_spec(context.strategy_id, context.strategy_version)
        atomic_write(
            output / "best-config.toml",
            best_config_bytes(
                context,
                registry.normalize_parameters(best.parameters, {}),
                output.parent / f"{output.name}-best-backtest",
            ),
        )
    return best


def selection_value(spec: StudySpec) -> dict[str, object]:
    return {
        "objective": {"metric": spec.metric, "direction": spec.direction},
        "constraints": [asdict(item) for item in spec.constraints],
    }


def computation_value(spec: StudySpec) -> dict[str, object]:
    return {
        "space": asdict(spec.space),
        "fixed_strategy": spec.fixed_strategy,
        "start_date": str(spec.start_date) if spec.start_date else None,
        "end_date": str(spec.end_date) if spec.end_date else None,
    }


def reselect_optimization(
    inputs: Sequence[Path],
    opt_config: Path,
    output: Path,
    existing: str = "error",
    *,
    dry_run: bool = False,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    if len(inputs) != 1:
        raise ValueError("OPT_RESELECT_INVALID: --opt-config requires exactly one --input")
    source, output = safe_output(inputs[0]), safe_output(output)
    validate_disjoint_paths({"source": source, "opt_config": opt_config}, {"output": output})
    source_sha = file_sha(source / "study_manifest.json")
    manifest = validate_study(source, require_complete=True)
    required = {
        "input-config.toml",
        "input-opt-config.toml",
        "config-template.toml",
        "candidates.json",
        "selected.json",
        "checkpoint.json",
        "baseline.json",
    }
    if not required <= manifest.get("files", {}).keys():
        raise ValueError("OPT_RESELECT_INVALID: source lacks original experiment snapshots")
    if not manifest.get("full_grid_complete") or manifest.get("failed_count") != 0:
        raise ValueError("OPT_RESELECT_INVALID: source must contain a complete successful grid")
    opt_bytes = opt_config.read_bytes()
    old_spec, spec = StudySpec.load(source / "input-opt-config.toml"), StudySpec.load(opt_config)
    if opt_config.read_bytes() != opt_bytes:
        raise ValueError("OPT_RESELECT_INVALID: selection configuration changed while reading")
    validate_objective(spec)
    if digest(computation_value(old_spec)) != digest(computation_value(spec)):
        raise ValueError("OPT_RESELECT_INVALID: only objective, constraints and workers may change")
    identity = manifest["identity"]
    if (
        file_sha(source / "input-config.toml") != identity["config_sha256"]
        or file_sha(source / "input-opt-config.toml") != identity["opt_config_sha256"]
    ):
        raise ValueError("OPT_RESELECT_INVALID: source configuration identity mismatch")
    candidates = read_json(source / "candidates.json")
    selected = read_json(source / "selected.json")
    values = read_json(source / "checkpoint.json")
    candidate_ids = {item["id"] for item in candidates}
    if (
        not candidates
        or digest(candidates) != identity["candidates_sha256"]
        or len(candidate_ids) != len(candidates)
        or len(selected) != len(candidates)
        or candidate_ids != {item["id"] for item in selected}
        or candidate_ids != set(values)
        or any(digest(item["parameters"]) != item["id"] for item in candidates)
        or digest(sorted(candidates, key=lambda item: item["id"]))
        != digest(sorted(selected, key=lambda item: item["id"]))
        or any(
            manifest.get(key) != len(candidates)
            for key in ("candidate_count", "selected_count", "completed_count")
        )
    ):
        raise ValueError("OPT_RESELECT_INVALID: source does not cover the complete candidate set")
    context = resolve_config(
        {"output": output.parent / f"{output.name}-best-backtest"},
        source / "config-template.toml",
        run_id=str(uuid.uuid4()),
        generated_at=datetime.now(UTC),
    )
    validate_disjoint_paths(
        {
            "source": source,
            "opt_config": opt_config,
            "research": context.research_artifact_path,
            **{f"factor-{i}": item.path for i, item in enumerate(context.custom_factor_inputs)},
        },
        {"output": output},
    )
    registry = resolve_strategy_spec(context.strategy_id, context.strategy_version)
    fields = sorted(set(old_spec.space.parameters) | set(old_spec.space.ordered_arrays))
    fixed = resolved_context_value(
        replace(
            context,
            parameters=registry.normalize_parameters(
                {**context.parameters, **old_spec.fixed_strategy}, {}
            ),
            analysis_periods=(),
        )
    )
    fixed.pop("output_alias")
    fixed["parameters"] = {
        key: value
        for key, value in cast(Mapping[str, object], fixed["parameters"]).items()
        if key not in fields
    }
    compatibility = identity["compatibility"]
    if (
        digest(fixed) != digest(compatibility["context"])
        or fields != compatibility["fields"]
        or digest(selection_value(old_spec))
        != digest({key: compatibility[key] for key in ("objective", "constraints")})
        or compatibility["metric_definition"] != "existing-backtest-252-rf0-filled-records-v1"
    ):
        raise ValueError("OPT_RESELECT_INVALID: saved inputs or metric definition do not match")
    evaluator = BacktestEvaluator(context)
    results = []
    artifact_paths = {}
    for candidate in candidates:
        item = result_from_value(values[candidate["id"]])
        if (
            item.id != candidate["id"]
            or item.status not in {"succeeded", "ineligible"}
            or digest(item.parameters) != digest(candidate["parameters"])
        ):
            raise ValueError("OPT_RESELECT_INVALID: checkpoint parameter or status mismatch")
        artifact = source / "trials" / item.id
        recovered = verified_trial(evaluator, item, artifact)
        reasons = spec.rejection_reasons(recovered.metrics)
        results.append(
            replace(
                recovered, status="ineligible" if reasons else "succeeded", reasons=tuple(reasons)
            )
        )
        artifact_paths[item.id] = str(artifact)
    baseline = verified_trial(
        evaluator, result_from_value(read_json(source / "baseline.json")), source / "baseline"
    )
    spec = replace(spec, start_date=context.start_date, end_date=context.end_date)
    eligible_count = sum(item.status == "succeeded" for item in results)
    progress(f"OPT_RESELECT reused={len(results)} eligible={eligible_count} new_backtests=0")
    input_bytes = (source / "input-config.toml").read_bytes()
    source_record = {
        "path": str(source),
        "manifest_sha256": source_sha,
        "code": compatibility["code"],
        "selection": selection_value(old_spec),
        "artifact_paths": artifact_paths,
    }
    report_identity = {
        "sources": [source_record],
        "compatibility": {**compatibility, **selection_value(spec)},
        "opt_config_sha256": hashlib.sha256(opt_bytes).hexdigest(),
    }

    def check_source() -> None:
        if file_sha(source / "study_manifest.json") != source_sha:
            raise ValueError("OPT_RESELECT_INVALID: source changed during reselection")

    check_source()
    if dry_run:
        return {"status": "dry_run", "reused_count": len(results), "eligible_count": eligible_count}
    with study_lock(output):
        check_source()
        if output.exists() and existing == "skip":
            previous = validate_study(output, require_complete=True, allow_reselection=True)
            if previous.get("operation") != "reselect" or digest(previous["identity"]) != digest(
                report_identity
            ):
                raise ValueError("OPT_RESULT_INVALID: existing reselection inputs differ")
            return previous
        prepare_output(output, existing, False)
        write_json(
            output / "study_manifest.json",
            {
                "schema_version": "1.0",
                "operation": "reselect",
                "status": "running",
                "identity": report_identity,
                "files": {},
            },
        )
        atomic_write(output / "input-config.toml", input_bytes)
        atomic_write(output / "input-opt-config.toml", opt_bytes)
        atomic_write(
            output / "config-template.toml",
            best_config_bytes(
                context, context.parameters, output.parent / f"{output.name}-best-backtest"
            ),
        )
        write_json(output / "sources.json", [source_record])
        old_conditions = (
            "; ".join(
                f"{item.metric} {item.operator} {item.value}" for item in old_spec.constraints
            )
            or "无"
        )
        publish_study_report(
            output,
            spec,
            results,
            len(candidates),
            context,
            baseline,
            notes=(
                "本报告复用历史回测指标, 不重新运行策略。",
                "",
                f"- 来源: {source}",
                f"- 原回测代码指纹: {compatibility['code']}",
                f"- 原目标: {old_spec.direction} {old_spec.metric}",
                f"- 原约束: {old_conditions}",
                f"- 复用组合: {len(results)}",
                "- 新增回测次数: 0",
            ),
            artifact_paths=artifact_paths,
        )
        check_source()
        result_manifest = {
            "schema_version": "1.0",
            "operation": "reselect",
            "status": "complete",
            "identity": report_identity,
            "candidate_count": len(candidates),
            "reused_count": len(results),
            "eligible_count": eligible_count,
            "new_backtest_count": 0,
            "files": collect_files(output),
        }
        write_json(output / "study_manifest.json", result_manifest)
        finish_backup(output)
    return result_manifest


def validate_period(context: ResolvedRunContext, candidates: list[dict[str, Any]]) -> None:
    """Read actual calendar and warmup data without creating a ResearchSession or files."""
    report = ResearchCheckService().check(context.research_artifact_path)
    if not report.valid:
        raise ValueError(f"DATA_INPUT_CORRUPT: {','.join(report.issue_codes)}")
    ArtifactReader().open(context.research_artifact_path)
    calendar = pq.read_table(
        context.research_artifact_path / "tables/trade_calendar.parquet",
        columns=["calendar_date", "is_open"],
    ).to_pylist()
    days = sorted({row["calendar_date"] for row in calendar if row["is_open"]})
    start, end = cast(date, context.start_date), cast(date, context.end_date)
    interval = [day for day in days if start <= day <= end]
    if len(interval) < 2:
        raise ValueError("DATA_COVERAGE_INSUFFICIENT: training period needs two trading days")
    # Holidays may border the training range; missing whole trading weeks may not.
    if (interval[0] - start).days > 10 or (end - interval[-1]).days > 10:
        raise ValueError("DATA_COVERAGE_INSUFFICIENT: calendar does not cover training range")
    spec = resolve_strategy_spec(context.strategy_id, context.strategy_version)
    longest = max(
        (spec.declaration(item["parameters"]) for item in candidates),
        key=lambda declaration: declaration.lookback_trade_days,
    )
    history = [day for day in days if day <= interval[0]][-longest.lookback_trade_days :]
    if len(history) < longest.lookback_trade_days:
        raise ValueError("STRATEGY_WARMUP_INSUFFICIENT: training start lacks warmup history")
    for requirement in longest.data_requirements:
        if requirement.dataset != "MARKET_HISTORY" or not requirement.security_scope.startswith(
            "SECURITY:"
        ):
            continue
        security_id = requirement.security_scope.split(":", 1)[1]
        rows = pq.read_table(
            context.research_artifact_path / "tables/market_daily.parquet",
            columns=["trade_date", "available_from", *requirement.fields],
            filters=[
                ("security_id", "=", security_id),
                ("trade_date", ">=", history[0]),
                ("trade_date", "<=", interval[0]),
            ],
        ).to_pylist()
        visible = {
            row["trade_date"]
            for row in rows
            if row["available_from"] <= interval[0]
            and all(row[field] is not None for field in requirement.fields)
        }
        if not set(history) <= visible:
            raise ValueError("DATA_COVERAGE_INSUFFICIENT: security warmup prices missing")


def collect_files(output: Path) -> dict[str, str]:
    return {
        path.name: file_sha(path)
        for path in output.iterdir()
        if path.is_file() and path.name not in {"study_manifest.json", "overwrite_backup.json"}
    }


def validate_study(
    output: Path, *, require_complete: bool = False, allow_reselection: bool = False
) -> dict[str, Any]:
    safe_output(output)
    manifest = read_json(output / "study_manifest.json")
    if manifest.get("operation") == "reselect" and not allow_reselection:
        raise ValueError("OPT_RESELECT_INVALID: use the original experiment, not a derived report")
    if require_complete and manifest["status"] != "complete":
        raise ValueError("OPT_RESULT_INVALID: experiment is not complete; use --resume")
    for name, expected in manifest.get("files", {}).items():
        if Path(name).name != name or file_sha(output / name) != expected:
            raise ValueError(f"OPT_RESULT_INVALID: changed experiment file {name}")
    return cast(dict[str, Any], manifest)


def validate_objective(spec: StudySpec) -> None:
    properties = SchemaRegistry().load_json_schema("metrics")["properties"]
    numeric_metrics = {
        key
        for key, value in properties.items()
        if value.get("type") in ("number", "integer")
        or (isinstance(value.get("type"), list) and "number" in value["type"])
    } | {"trade_count"}
    unknown = {spec.metric, *(item.metric for item in spec.constraints)} - numeric_metrics
    if unknown:
        raise ValueError(f"OPT_CONFIG_INVALID: unknown numeric metric {min(unknown)}")


def verified_trial(evaluator: BacktestEvaluator, item: TrialResult, artifact: Path) -> TrialResult:
    recovered = evaluator.recover(TrialJob(item.id, item.parameters, artifact))
    if (
        recovered is None
        or recovered.artifact_sha256 != item.artifact_sha256
        or digest(recovered.metrics) != digest(item.metrics)
    ):
        raise ValueError(f"OPT_RESULT_INVALID: trial result changed for {item.id}")
    return recovered


def run_optimization(
    *,
    config: Path,
    opt_config: Path,
    output: Path,
    start_date: date | None = None,
    end_date: date | None = None,
    workers: int | None = None,
    existing: str = "error",
    resume: bool = False,
    dry_run: bool = False,
    shard_count: int = 1,
    shard_index: int = 0,
    custom_factors: Sequence[Path] = (),
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    output = safe_output(output)
    spec = StudySpec.load(opt_config)
    validate_objective(spec)
    effective_workers = workers if workers is not None else spec.workers
    if effective_workers < 1 or shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("OPT_CONFIG_INVALID: invalid workers or shard selection")
    if resume and existing != "error":
        raise ValueError("OPT_CONFIG_INVALID: --resume conflicts with --existing skip/overwrite")
    base_context = resolve_config(
        {
            "mode": "BACKTEST",
            "output": output,
            "start_date": start_date or spec.start_date,
            "end_date": end_date or spec.end_date,
        },
        config,
        run_id=str(uuid.uuid4()),
        generated_at=datetime.now(UTC),
    )
    spec = replace(spec, start_date=base_context.start_date, end_date=base_context.end_date)
    if custom_factors:
        base_context = replace(
            base_context,
            custom_factor_inputs=tuple(
                CustomFactorInput(path.resolve(), file_sha(path)) for path in custom_factors
            ),
        )
    validate_disjoint_paths(
        {
            "config": config,
            "opt_config": opt_config,
            "research": base_context.research_artifact_path,
            **{
                f"factor-{i}": item.path for i, item in enumerate(base_context.custom_factor_inputs)
            },
        },
        {"output": output},
    )
    registry = resolve_strategy_spec(base_context.strategy_id, base_context.strategy_version)
    base = {**base_context.parameters, **spec.fixed_strategy}
    candidates = spec.space.generate(base, lambda values: registry.normalize_parameters(values, {}))
    fields = sorted(set(spec.space.parameters) | set(spec.space.ordered_arrays))
    if base_context.strategy_id == "staged_drawdown_v1":
        if "take_profit_levels" in fields and (
            base["take_profit_mode"] != "tiered"
            or len(cast(Sequence[object], base["take_profit_sell_fractions"])) != 3
        ):
            raise ValueError("OPT_CONFIG_INVALID: this search requires three-tier take profits")
        if (
            any(key.startswith("entry_confirmation_") for key in fields)
            and base["entry_confirmation_mode"] != "ma_rebound"
        ):
            raise ValueError("OPT_CONFIG_INVALID: entry confirmation search requires ma_rebound")
    validate_period(base_context, [*candidates, {"parameters": base_context.parameters}])
    selected = [item for index, item in enumerate(candidates) if index % shard_count == shard_index]
    if not selected:
        raise ValueError("OPT_CONFIG_INVALID: selected shard has no candidates")
    context_value = resolved_context_value(
        replace(
            base_context, parameters=registry.normalize_parameters(base, {}), analysis_periods=()
        )
    )
    context_value.pop("output_alias")
    fixed_context = {
        **context_value,
        "parameters": {
            key: value
            for key, value in cast(Mapping[str, object], context_value["parameters"]).items()
            if key not in fields
        },
    }
    compatibility = {
        "context": fixed_context,
        "fields": fields,
        "code": code_fingerprint(),
        "objective": {"metric": spec.metric, "direction": spec.direction},
        "constraints": [asdict(item) for item in spec.constraints],
        "metric_definition": "existing-backtest-252-rf0-filled-records-v1",
    }
    identity = {
        "config_sha256": file_sha(config),
        "opt_config_sha256": file_sha(opt_config),
        "compatibility": compatibility,
        "candidates_sha256": digest(candidates),
        "shard_count": shard_count,
        "shard_index": shard_index,
    }
    # workers are operational, not experiment identity; opt file edits still change its byte hash.
    progress(
        f"OPT_PLAN start={base_context.start_date} end={base_context.end_date} "
        f"candidates={len(candidates)} selected={len(selected)} workers={effective_workers} "
        f"fixed_strategy={dict(spec.fixed_strategy)}"
    )
    if dry_run:
        return {
            "status": "dry_run",
            "candidate_count": len(candidates),
            "selected_count": len(selected),
        }
    evaluator = BacktestEvaluator(base_context)
    jobs = [
        TrialJob(item["id"], item["parameters"], output / "trials" / item["id"])
        for item in selected
    ]
    baseline_job = TrialJob("baseline", base_context.parameters, output / "baseline")
    with study_lock(output):
        if output.exists() and existing == "skip" and not resume:
            manifest = validate_study(output, require_complete=True)
            if digest(manifest["identity"]) != digest(identity):
                raise ValueError("OPT_RESULT_INVALID: existing experiment inputs differ")
            for job in [baseline_job, *jobs]:
                if evaluator.recover(job) is None:
                    raise ValueError("OPT_RESULT_INVALID: completed trial missing")
            return manifest
        if resume:
            manifest = validate_study(output)
            if digest(manifest["identity"]) != digest(identity):
                raise ValueError(
                    "OPT_RESUME_INVALID: config, code, data or candidate inputs changed"
                )
        prepare_output(output, existing, resume)
        if not resume:
            atomic_write(output / "input-config.toml", config.read_bytes())
            atomic_write(output / "input-opt-config.toml", opt_config.read_bytes())
            write_json(output / "candidates.json", candidates)
            write_json(output / "selected.json", selected)
            # Reusable export template for merging without dependencies on the original config path.
            atomic_write(
                output / "config-template.toml",
                best_config_bytes(
                    base_context,
                    base_context.parameters,
                    output.parent / f"{output.name}-best-backtest",
                ),
            )
        manifest = {
            "schema_version": "1.0",
            "identity": identity,
            "status": "running",
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "workers": effective_workers,
            "files": {},
        }
        write_json(output / "study_manifest.json", manifest)
        try:
            baseline = evaluator.recover(baseline_job)
            if baseline is None:
                started = time.monotonic()
                baseline = evaluator(baseline_job)
                elapsed = time.monotonic() - started
                estimate = elapsed * len(jobs) / effective_workers
                progress(
                    f"OPT_BASELINE elapsed={elapsed:.1f}s estimated_parallel_seconds={estimate:.1f}"
                )
            write_json(output / "baseline.json", asdict(baseline))
            results = run_trials(jobs, evaluator, spec, output, effective_workers, progress)
            publish_study_report(output, spec, results, len(candidates), base_context, baseline)
            failures = sum(item.status == "failed" for item in results)
            manifest.update(
                status="failed" if failures else "complete",
                completed_count=len(results),
                failed_count=failures,
                eligible_count=sum(item.status == "succeeded" for item in results),
                full_grid_complete=not failures and len(results) == len(candidates),
            )
            if not failures:
                finish_backup(output)
        except BaseException as error:
            manifest.update(
                status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                error=str(error),
            )
            if (output / "checkpoint.json").exists():
                partial = [
                    result_from_value(item)
                    for item in read_json(output / "checkpoint.json").values()
                ]
                publish_study_report(output, spec, partial, len(candidates), base_context)
            raise
        finally:
            manifest["files"] = collect_files(output)
            write_json(output / "study_manifest.json", manifest)
    return manifest


def merge_optimizations(
    inputs: Sequence[Path],
    output: Path,
    existing: str = "error",
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    output = safe_output(output)
    if not inputs:
        raise ValueError("OPT_CONFIG_INVALID: experiment inputs are required")
    validate_disjoint_paths({str(i): path for i, path in enumerate(inputs)}, {"output": output})
    manifests = [validate_study(path) for path in inputs]
    compatibility = manifests[0]["identity"]["compatibility"]
    if any(
        digest(item["identity"]["compatibility"]) != digest(compatibility) for item in manifests
    ):
        raise ValueError("OPT_MERGE_INVALID: incompatible experiment inputs")
    results: dict[str, TrialResult] = {}
    candidate_ids: set[str] = set()
    sources = []
    for path, manifest in zip(inputs, manifests, strict=True):
        candidates = read_json(path / "candidates.json")
        candidate_ids.update(item["id"] for item in candidates)
        template_context = resolve_config(
            {"output": path / "unused-merge-context"},
            path / "config-template.toml",
            run_id=str(uuid.uuid4()),
            generated_at=datetime.now(UTC),
        )
        evaluator = BacktestEvaluator(template_context)
        values = read_json(path / "checkpoint.json") if (path / "checkpoint.json").exists() else {}
        for value in values.values():
            item = result_from_value(value)
            if item.status != "failed":
                verified_trial(evaluator, item, path / "trials" / item.id)
            previous = results.get(item.id)
            if previous is not None:
                if (
                    previous.status != "failed"
                    and item.status != "failed"
                    and digest(previous.metrics) != digest(item.metrics)
                ):
                    raise ValueError("OPT_MERGE_INVALID: duplicate parameter metrics conflict")
                if item.status == "failed":
                    continue
            results[item.id] = item
        source_spec = StudySpec.load(path / "input-opt-config.toml")
        sources.append(
            {
                "path": str(path.resolve()),
                "manifest_sha256": file_sha(path / "study_manifest.json"),
                "candidate_count": len(candidates),
                "selected_count": manifest["selected_count"],
                "search_space": {
                    "parameters": source_spec.space.parameters,
                    "ordered_arrays": source_spec.space.ordered_arrays,
                },
            }
        )
    spec = StudySpec.load(inputs[0] / "input-opt-config.toml")
    identity = {"compatibility": compatibility, "sources": sources}
    if dry_run:
        return {"status": "dry_run", "unique_evaluated_count": len(results)}
    with study_lock(output):
        if output.exists() and existing == "skip":
            previous_manifest = validate_study(output, require_complete=True)
            if digest(previous_manifest["identity"]) != digest(identity):
                raise ValueError("OPT_RESULT_INVALID: merge inputs changed")
            return previous_manifest
        prepare_output(output, existing, False)
        write_json(output / "sources.json", sources)
        baseline_path = inputs[0] / "baseline.json"
        baseline = result_from_value(read_json(baseline_path)) if baseline_path.exists() else None
        context = resolve_config(
            {"output": output.parent / f"{output.name}-best-backtest"},
            inputs[0] / "config-template.toml",
            run_id=str(uuid.uuid4()),
            generated_at=datetime.now(UTC),
        )
        spec = replace(spec, start_date=context.start_date, end_date=context.end_date)
        publish_study_report(
            output, spec, list(results.values()), len(candidate_ids), context, baseline
        )
        complete = len(results) == len(candidate_ids) and all(
            item.status != "failed" for item in results.values()
        )
        manifest = {
            "schema_version": "1.0",
            "status": "complete" if complete else "incomplete",
            "identity": identity,
            "candidate_count": len(candidate_ids),
            "unique_evaluated_count": len(results),
            "total_recorded_trials": sum(
                len(read_json(path / "checkpoint.json"))
                if (path / "checkpoint.json").exists()
                else 0
                for path in inputs
            ),
        }
        if complete:
            finish_backup(output)
        manifest["files"] = collect_files(output)
        write_json(output / "study_manifest.json", manifest)
    return manifest
