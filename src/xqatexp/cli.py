from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from xqatexp.application.services import SelfCheckService


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
    for name in ("fetch", "capabilities", "check-raw", "build", "update", "check-research"):
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
    print(f"{args.command} command is not implemented yet", file=sys.stderr)
    return 10
