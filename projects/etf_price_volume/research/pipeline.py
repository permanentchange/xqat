# Chinese report punctuation is intentional.
# ruff: noqa: RUF001
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.optimization.storage import atomic_write, safe_output, study_lock
from xqatexp.security import validate_disjoint_paths

from .conditional import analyze, build_groups
from .config import PROJECT, ROOT, Settings
from .data import load_data
from .features import FEATURES, build_features
from .labels import build_labels
from .reporting import markdown, plot_reports, write_csv
from .statistics import analyze_statistics

STAGES = ("data", "features", "labels", "conditional", "statistics", "report")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text("utf-8"))


def write(path: Path, value: object) -> None:
    atomic_write(path, canonical_json_bytes(value))


def identity(s: Settings) -> dict:
    opened = ArtifactReader().open(s.input)
    source = {
        "manifest_sha256": sha(s.input / "manifest.json"),
        "files": {f: sha(s.input / f) for f in opened.verified_files},
    }
    code = {
        str(p.relative_to(ROOT)): sha(p)
        for p in sorted(PROJECT.rglob("*.py"))
        if "tests" not in p.parts
    }
    for relative in (
        "src/xqatexp/artifacts/readers.py",
        "src/xqatexp/artifacts/manifest.py",
        "src/xqatexp/optimization/storage.py",
        "src/xqatexp/security.py",
    ):
        code[relative] = sha(ROOT / relative)
    for relative in ("protocol.md", "requirements.lock"):
        code[relative] = sha(PROJECT / relative)
    return {
        "schema_version": "1.0",
        "artifact_kind": "ETF_P0_RESEARCH",
        "config_sha256": sha(s.source),
        "config": s.raw,
        "input": str(s.input),
        "source": source,
        "code": code,
        "python": sys.version,
        "dependencies": {
            p: importlib.metadata.version(p)
            for p in ("numpy", "pyarrow", "scipy", "statsmodels", "matplotlib")
        },
    }


def verify_stage(path: Path, expected: dict) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("RESEARCH_RESULT_CORRUPT: unsafe/missing stage")
    record = read(path / "manifest.json")
    if record != expected:
        raise ValueError("RESEARCH_RESULT_CORRUPT: stage manifest mismatch")
    actual = {
        p.name: sha(p)
        for p in sorted(path.iterdir())
        if p.name != "manifest.json" and p.is_file() and not p.is_symlink()
    }
    if actual != record["files"] or any(p.is_symlink() or p.is_dir() for p in path.iterdir()):
        raise ValueError("RESEARCH_RESULT_CORRUPT: stage file hashes")


def run(s: Settings, output: Path, resume: bool, progress=print) -> Path:
    output = safe_output(output)
    inputs = {"research": s.input, "project_code": PROJECT, "config": s.source}
    validate_disjoint_paths(inputs, {"output": output})
    fingerprint = identity(s)
    with study_lock(output):
        if output.exists():
            if not resume:
                raise ValueError("RESEARCH_OUTPUT_EXISTS: use a new output or --resume")
            if output.is_symlink():
                raise ValueError("RESEARCH_UNSAFE_OUTPUT")
            manifest = read(output / "manifest.json")
            if manifest["identity"] != fingerprint:
                raise ValueError(
                    "RESEARCH_RESUME_INCOMPATIBLE: input/config/code/dependencies changed"
                )
            if manifest["status"] == "complete" and (
                not (output / "report.md").is_file()
                or (output / "report.md").is_symlink()
                or sha(output / "report.md") != manifest["report_sha256"]
            ):
                raise ValueError("RESEARCH_RESULT_CORRUPT: root report hash")
        else:
            if resume:
                raise ValueError("RESEARCH_RESUME_INVALID: output missing")
            output.mkdir(parents=True)
            manifest = {"identity": fingerprint, "status": "running", "stages": {}}
            write(output / "manifest.json", manifest)
        for stage in STAGES:
            destination = output / stage
            if stage in manifest["stages"]:
                verify_stage(destination, manifest["stages"][stage])
                progress(f"RESEARCH_REUSED stage={stage}")
            else:
                manifest["status"] = "running"
                write(output / "manifest.json", manifest)
                predecessor = {k: v for k, v in manifest["stages"].items()}
                # Recover a fully published stage after an interruption before root checkpoint.
                if destination.exists():
                    record = read(destination / "manifest.json")
                    if (
                        record["identity_sha256"]
                        != hashlib.sha256(canonical_json_bytes(fingerprint)).hexdigest()
                        or record["predecessors"] != predecessor
                    ):
                        raise ValueError("RESEARCH_RESULT_CORRUPT: uncheckpointed stage identity")
                    verify_stage(destination, record)
                else:
                    temporary = output / f".{stage}.staging-{uuid.uuid4().hex}"
                    temporary.mkdir()
                    try:
                        produce(stage, temporary, output, s, progress)
                        if identity(s) != fingerprint:
                            raise ValueError("RESEARCH_INPUT_CHANGED: publication cancelled")
                        record = {
                            "stage": stage,
                            "identity_sha256": hashlib.sha256(
                                canonical_json_bytes(fingerprint)
                            ).hexdigest(),
                            "predecessors": predecessor,
                            "files": {p.name: sha(p) for p in sorted(temporary.iterdir())},
                        }
                        write(temporary / "manifest.json", record)
                        temporary.rename(destination)
                    finally:
                        if temporary.exists():
                            shutil.rmtree(temporary)
                manifest["stages"][stage] = record
                write(output / "manifest.json", manifest)
                progress(f"RESEARCH_WRITTEN stage={stage}")
            if stage == "data" and not read(destination / "quality.json")["valid"]:
                manifest["status"] = "blocked_data"
                write(output / "manifest.json", manifest)
                raise ValueError("RESEARCH_DATA_INVALID: see data/quality.json")
        if identity(s) != fingerprint:
            raise ValueError("RESEARCH_INPUT_CHANGED")
        # The root report is a convenience copy of the verified report stage.
        atomic_write(output / "report.md", (output / "report" / "report.md").read_bytes())
        manifest.update(status="complete", report_sha256=sha(output / "report.md"))
        write(output / "manifest.json", manifest)
    progress(f"RESEARCH_COMPLETE output={output}")
    return output


def _development(table: pa.Table, s: Settings) -> pa.Table:
    dates = table["date"].to_pylist()
    return table.filter(pa.array([s.start <= d <= s.end for d in dates]))


def produce(stage: str, path: Path, output: Path, s: Settings, progress) -> None:
    if stage == "data":
        bars, quality = load_data(s)
        pq.write_table(bars, path / "bars.parquet")
        write(path / "quality.json", quality)
        (path / "quality.md").write_text(
            "# 数据基本检查\n\n"
            + f"有效：{quality['valid']}；开发期行数：{quality['development_rows']}。\n\n"
            + f"错误：{quality['errors']}\n\n提示：{quality['warnings']}\n\n"
            + "数据未修复或填充。收益为复权价格代理；分红及拆分事件尚未完整核验。\n",
            "utf-8",
        )
    elif stage == "features":
        bars = pq.read_table(output / "data/bars.parquet")
        f, reasons = build_features(bars)
        groups, group_reasons = build_groups(f, s.analysis["quantile_window"])
        pq.write_table(f, path / "features_with_warmup.parquet")
        pq.write_table(_development(f, s), path / "features.parquet")
        pq.write_table(groups, path / "groups.parquet")
        write_csv(path / "missing_features.csv", reasons)
        write_csv(path / "missing_groups.csv", group_reasons)
        write(path / "definitions.json", FEATURES)
    elif stage == "labels":
        bars = pq.read_table(output / "data/bars.parquet")
        labels, exclusions = build_labels(bars, s.analysis["horizons"], s.end)
        pq.write_table(labels, path / "labels_with_warmup.parquet")
        pq.write_table(_development(labels, s), path / "labels.parquet")
        write_csv(path / "exclusions.csv", exclusions)
    else:
        f = pq.read_table(output / "features/features_with_warmup.parquet")
        labels = pq.read_table(output / "labels/labels_with_warmup.parquet")
        groups = pq.read_table(output / "features/groups.parquet")
        if stage == "conditional":
            tables = analyze(f, labels, groups, s)
            write(path / "tables.json", tables)
            for name, rows in tables.items():
                write_csv(path / f"{name}.csv", rows)
        elif stage == "statistics":
            tests, candidates = analyze_statistics(f, labels, groups, s, progress)
            write(path / "tests.json", tests)
            write_csv(path / "tests.csv", tests)
            write(path / "candidates.json", candidates)
        elif stage == "report":
            tables = read(output / "conditional/tables.json")
            tests = read(output / "statistics/tests.json")
            candidates = read(output / "statistics/candidates.json")
            plot_reports(path, f, tables, s)
            quality = read(output / "data/quality.json")
            (path / "report.md").write_text(markdown(quality, tests, candidates, s), "utf-8")


def describe(s: Settings) -> dict:
    _, quality = load_data(s)
    count = 14 * len(s.analysis["horizons"]) * 3 + 5 * len(s.analysis["joint_horizons"]) * 3 * 2
    return {
        "stages": STAGES,
        "input": str(s.input),
        "security_id": s.security_id,
        "development": [s.start, s.end],
        "holdout_start": s.holdout,
        "tests": count,
        "workers": s.workers,
        "bootstrap_samples": s.analysis["bootstrap_samples"],
        "quality": quality,
    }


def configure_threads() -> None:
    # Spawned workers inherit these limits before importing their numerical libraries.
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[name] = "1"
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
