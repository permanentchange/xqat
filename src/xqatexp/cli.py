from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tomllib
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from xqatexp.application.services import SelfCheckService
from xqatexp.application.workflows import StrategyWorkflowService
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublishError
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.config import resolve_config
from xqatexp.domain.contracts import CustomFactorInput
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.capability import CapabilityProbe
from xqatexp.providers.tushare.client import TushareClient, TushareError
from xqatexp.providers.tushare.raw import FetchRequest, RawCheckService, RawFetchService
from xqatexp.providers.tushare.registry import dataset_ids
from xqatexp.reporting.readers import load_target
from xqatexp.research.custom_factors import CustomFactorCheckService
from xqatexp.research.tables import ResearchBuildConfig, ResearchBuilder, ResearchCheckService
from xqatexp.security import SecurityError, load_tushare_token


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xqatexp",
        description="Local A-share quantitative research and decision tool.",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    self_check = commands.add_parser("self-check", help="Validate the local installation.")
    self_check.add_argument("--offline", action="store_true", help="Forbid network checks.")

    data = commands.add_parser("data", help="Fetch, build, and inspect data.")
    data_commands = data.add_subparsers(dest="data_command", metavar="COMMAND")
    fetch = data_commands.add_parser("fetch")
    fetch.add_argument("--dataset", required=True, choices=dataset_ids())
    fetch.add_argument("--start", required=True, type=date.fromisoformat)
    fetch.add_argument("--end", required=True, type=date.fromisoformat)
    fetch.add_argument("--output", required=True, type=Path)
    fetch.add_argument("--existing", choices=("error", "skip", "overwrite"), default="error")
    capabilities = data_commands.add_parser("capabilities")
    capabilities.add_argument("--output", required=True, type=Path)
    capabilities.add_argument("--trade-date")
    check_raw = data_commands.add_parser("check-raw")
    check_raw.add_argument("--input", required=True, type=Path)
    check_raw.add_argument("--report", required=True, type=Path)
    build = data_commands.add_parser("build")
    build.add_argument("--raw-root", required=True, action="append", type=Path)
    build.add_argument("--config", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--existing", choices=("error", "skip", "overwrite"), default="error")
    update = data_commands.add_parser("update")
    update.add_argument("--base", required=True, type=Path)
    update.add_argument("--raw-root", action="append", default=[], type=Path)
    update.add_argument("--config", required=True, type=Path)
    update.add_argument("--output", required=True, type=Path)
    update.add_argument("--existing", choices=("error", "skip", "overwrite"), default="error")
    check_research = data_commands.add_parser("check-research")
    check_research.add_argument("--input", required=True, type=Path)
    check_research.add_argument("--report", required=True, type=Path)

    factor = commands.add_parser("factor", help="Validate user custom factors.")
    factor_check = factor.add_subparsers(dest="factor_command", metavar="COMMAND").add_parser(
        "check"
    )
    factor_check.add_argument("--file", required=True, type=Path)
    factor_check.add_argument("--research", required=True, type=Path)
    factor_check.add_argument("--strategy", default="weekly_market_guard_rank_v1")
    factor_check.add_argument("--start", required=True, type=date.fromisoformat)
    factor_check.add_argument("--end", required=True, type=date.fromisoformat)
    factor_check.add_argument("--report", required=True, type=Path)

    backtest = commands.add_parser("backtest", help="Run a historical evaluation.")
    backtest_run = backtest.add_subparsers(dest="backtest_command", metavar="COMMAND").add_parser(
        "run"
    )
    _add_run_arguments(backtest_run, dates="range")

    daily = commands.add_parser("daily", help="Generate daily target or advice artifacts.")
    daily_commands = daily.add_subparsers(dest="daily_command", metavar="COMMAND")
    daily_target = daily_commands.add_parser("target")
    _add_run_arguments(daily_target, dates="decision")
    daily_target.add_argument("--previous-target", type=Path)
    daily_advice = daily_commands.add_parser("advise")
    daily_advice.add_argument("--config", required=True, type=Path)
    daily_advice.add_argument("--target", required=True, type=Path)
    daily_advice.add_argument("--account", type=Path)
    daily_advice.add_argument("--output", required=True, type=Path)
    daily_advice.add_argument("--existing", choices=("error", "skip", "overwrite"), default="error")

    result = commands.add_parser("result", help="Inspect a published result artifact.")
    show = result.add_subparsers(dest="result_command", metavar="COMMAND").add_parser("show")
    show.add_argument("--input", required=True, type=Path)
    show.add_argument("--format", choices=("summary", "markdown", "json"), default="summary")
    return parser


def _add_run_arguments(parser: argparse.ArgumentParser, *, dates: str) -> None:
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    if dates == "range":
        parser.add_argument("--start-date", type=date.fromisoformat)
        parser.add_argument("--end-date", type=date.fromisoformat)
    else:
        parser.add_argument("--decision-date", required=True, type=date.fromisoformat)
    parser.add_argument("--custom-factor", action="append", type=Path, default=[])
    parser.add_argument("--existing", choices=("error", "skip", "overwrite"), default="error")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "self-check":
        try:
            result = SelfCheckService().run(offline=args.offline)
        except Exception as error:
            print(str(error), file=sys.stderr)
            return 3
        print(f"SELF_CHECK_OK checks={','.join(result.checks)}")
        return 0
    if args.command == "data":
        return _run_data(args)
    if args.command == "factor":
        return _run_factor(args)
    if args.command in {"backtest", "daily"}:
        return _run_strategy(args)
    if args.command == "result" and args.result_command == "show":
        return _show_result(args.input, args.format)
    print(f"{args.command} command is not implemented yet", file=sys.stderr)
    return 10


def _run_factor(args: argparse.Namespace) -> int:
    if args.factor_command != "check":
        return 10
    report = CustomFactorCheckService().check(args.file, args.research, args.start, args.end)
    try:
        _write_new(
            args.report,
            canonical_json_bytes(
                {
                    "schema_version": "1.0",
                    "valid": report.valid,
                    "factor_names": list(report.factor_names),
                    "row_count": report.row_count,
                    "minimum_coverage": report.minimum_coverage,
                    "issue_codes": list(report.issue_codes),
                }
            ),
        )
    except (OSError, ArtifactPublishError) as error:
        print(str(error), file=sys.stderr)
        return 4
    print(f"FACTOR_CHECK valid={str(report.valid).lower()} report={args.report}")
    return 0 if report.valid else 3


def _run_strategy(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    try:
        if args.command == "backtest":
            cli_values = {
                "mode": "BACKTEST",
                "output": args.output,
                "start_date": args.start_date,
                "end_date": args.end_date,
            }
            context = resolve_config(
                cli_values, args.config, run_id=str(uuid.uuid4()), generated_at=now
            )
            if args.custom_factor:
                context = replace(
                    context,
                    custom_factor_inputs=_custom_inputs(args.custom_factor),
                )
            output = StrategyWorkflowService().backtest(
                context, OverwritePolicy(args.existing.upper())
            )
        elif args.daily_command == "target":
            cli_values = {
                "mode": "DAILY_TARGET",
                "output": args.output,
                "decision_date": args.decision_date,
            }
            context = resolve_config(
                cli_values, args.config, run_id=str(uuid.uuid4()), generated_at=now
            )
            custom = _custom_inputs(args.custom_factor)
            context = replace(
                context,
                custom_factor_inputs=custom or context.custom_factor_inputs,
                previous_target_path=args.previous_target or context.previous_target_path,
            )
            output = StrategyWorkflowService().daily_target(
                context, OverwritePolicy(args.existing.upper())
            )
        else:
            target = load_target(args.target)
            cli_values = {
                "mode": "DAILY_ADVICE",
                "output": args.output,
                "decision_date": target.decision_date,
            }
            context = resolve_config(
                cli_values, args.config, run_id=str(uuid.uuid4()), generated_at=now
            )
            output = StrategyWorkflowService().daily_advice(
                context,
                args.target,
                args.account,
                OverwritePolicy(args.existing.upper()),
                now,
            )
        print(f"RESULT_WRITTEN output={output}")
        return 0
    except ArtifactPublishError as error:
        print(str(error), file=sys.stderr)
        return 4
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return (
            3
            if str(error).split(":", 1)[0]
            in {
                "DATA_REQUIRED_MISSING",
                "DATA_COVERAGE_INSUFFICIENT",
                "STRATEGY_WARMUP_INSUFFICIENT",
                "FACTOR_COVERAGE_INSUFFICIENT",
            }
            else 2
        )


def _custom_inputs(paths: Sequence[Path]) -> tuple[CustomFactorInput, ...]:
    return tuple(
        CustomFactorInput(path.resolve(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in paths
    )


def _show_result(path: Path, output_format: str) -> int:
    try:
        opened = ArtifactReader().open(path)
        if output_format == "json":
            print(canonical_json_bytes(opened.manifest).decode("utf-8"), end="")
        elif output_format == "markdown":
            print((opened.path / "report.md").read_text(encoding="utf-8"), end="")
        else:
            print(f"RESULT type={opened.manifest['artifact_type']} input={opened.path.name}")
            report = opened.path / "report.md"
            if report.is_file():
                print(report.read_text(encoding="utf-8"), end="")
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 4


def _run_data(args: argparse.Namespace) -> int:
    try:
        if args.data_command == "capabilities":
            token = load_tushare_token(os.environ)
            probe_date = args.trade_date or _previous_weekday(date.today()).strftime("%Y%m%d")
            results = CapabilityProbe(TushareClient(token)).run(
                dataset_ids(), trade_date=probe_date
            )
            value = {
                "schema_version": "1.0",
                "provider": "tushare",
                "probe_trade_date": probe_date,
                "capabilities": [
                    {
                        "dataset_id": item.dataset_id,
                        "api_name": item.api_name,
                        "status": item.status.value,
                        "returned_fields": list(item.returned_fields),
                        "row_count": item.row_count,
                        "evidence_code": item.evidence_code,
                    }
                    for item in results
                ],
            }
            _write_new(args.output, canonical_json_bytes(value))
            print(f"CAPABILITIES_WRITTEN output={args.output}")
            return 0
        if args.data_command == "fetch":
            token = load_tushare_token(os.environ)
            request = FetchRequest(
                dataset_id=args.dataset,
                start_date=args.start,
                end_date=args.end,
                security_ids=(),
                fields=(),
                output_path=args.output,
                existing_policy=OverwritePolicy(args.existing.upper()),
            )
            published = RawFetchService(TushareClient(token)).fetch(request)
            print(f"RAW_WRITTEN output={published.path}")
            return 0
        if args.data_command == "check-raw":
            raw_report = RawCheckService().check(args.input)
            _write_new(
                args.report,
                canonical_json_bytes(
                    {
                        "schema_version": "1.0",
                        "valid": raw_report.valid,
                        "row_count": raw_report.row_count,
                        "issue_codes": list(raw_report.issue_codes),
                    }
                ),
            )
            print(f"RAW_CHECK valid={str(raw_report.valid).lower()} report={args.report}")
            return 0 if raw_report.valid else 3
        if args.data_command == "check-research":
            research_report = ResearchCheckService().check(args.input)
            _write_new(
                args.report,
                canonical_json_bytes(
                    {
                        "schema_version": "1.0",
                        "valid": research_report.valid,
                        "table_rows": research_report.table_rows,
                        "issue_codes": list(research_report.issue_codes),
                    }
                ),
            )
            print(f"RESEARCH_CHECK valid={str(research_report.valid).lower()} report={args.report}")
            return 0 if research_report.valid else 3
        if args.data_command in {"build", "update"}:
            build_config = _research_build_config(args.config, args.existing)
            builder = ResearchBuilder()
            if args.data_command == "build":
                published = builder.build(tuple(args.raw_root), build_config, args.output)
            else:
                published = builder.update(
                    args.base, tuple(args.raw_root), build_config, args.output
                )
            print(f"RESEARCH_WRITTEN output={published.path}")
            return 0
    except SecurityError as error:
        print(str(error), file=sys.stderr)
        return 3
    except TushareError as error:
        print(str(error), file=sys.stderr)
        return 5
    except ArtifactPublishError as error:
        print(str(error), file=sys.stderr)
        return 4
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print("data command is not implemented yet", file=sys.stderr)
    return 10


def _previous_weekday(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _research_build_config(path: Path, existing: str) -> ResearchBuildConfig:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    strategy = raw.get("strategy")
    if raw.get("schema_version") != "1.0" or not isinstance(strategy, dict):
        raise ValueError("CONFIG_SCHEMA_INVALID: research build config")
    try:
        start = date.fromisoformat(str(raw["start_date"]))
        end = date.fromisoformat(str(raw["end_date"]))
        etf_id = str(strategy["csi300_etf_id"])
    except (KeyError, ValueError) as error:
        raise ValueError("CONFIG_VALUE_INVALID: research date range or ETF id") from error
    return ResearchBuildConfig(start, end, etf_id, OverwritePolicy(existing.upper()))


def _write_new(path: Path, payload: bytes) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ArtifactPublishError(f"ARTIFACT_OUTPUT_EXISTS: {target.name}")
    temporary = target.with_name(f".{target.name}.staging")
    try:
        temporary.write_bytes(payload)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
