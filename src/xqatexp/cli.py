from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from xqatexp.application.services import SelfCheckService
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublishError
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.capability import CapabilityProbe
from xqatexp.providers.tushare.client import TushareClient, TushareError
from xqatexp.providers.tushare.raw import FetchRequest, RawCheckService, RawFetchService
from xqatexp.providers.tushare.registry import dataset_ids
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
    for name in ("build", "update", "check-research"):
        data_commands.add_parser(name)

    factor = commands.add_parser("factor", help="Validate user custom factors.")
    factor.add_subparsers(dest="factor_command", metavar="COMMAND").add_parser("check")

    backtest = commands.add_parser("backtest", help="Run a historical evaluation.")
    backtest.add_subparsers(dest="backtest_command", metavar="COMMAND").add_parser("run")

    daily = commands.add_parser("daily", help="Generate daily target or advice artifacts.")
    daily_commands = daily.add_subparsers(dest="daily_command", metavar="COMMAND")
    daily_commands.add_parser("target")
    daily_commands.add_parser("advise")

    result = commands.add_parser("result", help="Inspect a published result artifact.")
    result.add_subparsers(dest="result_command", metavar="COMMAND").add_parser("show")
    return parser


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
    print(f"{args.command} command is not implemented yet", file=sys.stderr)
    return 10


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
            report = RawCheckService().check(args.input)
            _write_new(
                args.report,
                canonical_json_bytes(
                    {
                        "schema_version": "1.0",
                        "valid": report.valid,
                        "row_count": report.row_count,
                        "issue_codes": list(report.issue_codes),
                    }
                ),
            )
            print(f"RAW_CHECK valid={str(report.valid).lower()} report={args.report}")
            return 0 if report.valid else 3
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
