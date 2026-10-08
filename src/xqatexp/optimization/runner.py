from __future__ import annotations

import multiprocessing
import signal
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .models import StudySpec, TrialResult
from .storage import read_json, write_json


@dataclass(frozen=True)
class TrialJob:
    id: str
    parameters: Mapping[str, object]
    output: Path


class Evaluator(Protocol):
    def __call__(self, job: TrialJob) -> TrialResult: ...

    def recover(self, job: TrialJob) -> TrialResult | None: ...


def initialize_worker() -> None:
    import pyarrow as pa

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def evaluate(evaluator: Evaluator, job: TrialJob, spec: StudySpec) -> TrialResult:
    try:
        result = evaluator(job)
        reasons = spec.rejection_reasons(result.metrics)
        return TrialResult(
            result.id,
            result.parameters,
            result.metrics,
            "ineligible" if reasons else "succeeded",
            tuple(reasons),
            result.artifact_sha256,
        )
    except Exception as error:
        return TrialResult(job.id, job.parameters, {}, "failed", (str(error),))


def run_trials(
    jobs: Sequence[TrialJob],
    evaluator: Evaluator,
    spec: StudySpec,
    output: Path,
    workers: int,
    progress: Callable[[str], None] = print,
) -> list[TrialResult]:
    checkpoint = output / "checkpoint.json"
    saved = read_json(checkpoint) if checkpoint.exists() else {}
    results: dict[str, TrialResult] = {}
    pending = []
    started = time.monotonic()

    def save(result: TrialResult) -> None:
        results[result.id] = result
        write_json(checkpoint, {key: asdict(value) for key, value in sorted(results.items())})
        eligible = [item for item in results.values() if item.status == "succeeded"]
        best = min(eligible, key=spec.rank_key).metrics[spec.metric] if eligible else None
        failures = sum(item.status == "failed" for item in results.values())
        progress(
            f"OPT_PROGRESS completed={len(results)}/{len(jobs)} eligible={len(eligible)} "
            f"failed={failures} best_{spec.metric}={best} elapsed={time.monotonic() - started:.1f}s"
        )

    for job in jobs:
        recovered = evaluator.recover(job)
        previous = saved.get(job.id)
        if recovered is not None:
            if previous and previous.get("artifact_sha256") not in (
                None,
                recovered.artifact_sha256,
            ):
                raise ValueError(f"OPT_RESULT_INVALID: artifact changed for {job.id}")
            reasons = spec.rejection_reasons(recovered.metrics)
            results[job.id] = TrialResult(
                job.id,
                job.parameters,
                recovered.metrics,
                "ineligible" if reasons else "succeeded",
                tuple(reasons),
                recovered.artifact_sha256,
            )
        elif previous and previous["status"] in ("succeeded", "ineligible"):
            raise ValueError(f"OPT_RESULT_INVALID: completed artifact missing for {job.id}")
        else:
            pending.append(job)
    write_json(checkpoint, {key: asdict(value) for key, value in sorted(results.items())})
    if workers == 1:
        for job in pending:
            save(evaluate(evaluator, job, spec))
    elif pending:
        pool = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=initialize_worker,
        )
        futures = {pool.submit(evaluate, evaluator, job, spec): job for job in pending}
        try:
            for future in as_completed(futures):
                job = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    result = TrialResult(job.id, job.parameters, {}, "failed", (str(error),))
                save(result)
        except BaseException:
            for future in futures:
                future.cancel()
            # Windows and Python 3.12 have no public terminate_workers API.
            processes = list((getattr(pool, "_processes", None) or {}).values())
            for process in processes:
                if process.is_alive():
                    process.terminate()
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)
    return [results[job.id] for job in jobs]
