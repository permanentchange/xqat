from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Set before NumPy/BLAS imports, in both coordinator and spawned workers.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main(argv: list[str] | None = None) -> int:
    from projects.etf_price_volume.research.config import load
    from projects.etf_price_volume.research.pipeline import configure_threads, describe, run
    from xqatexp.artifacts.manifest import canonical_json_bytes

    parser = argparse.ArgumentParser(description="Offline single-ETF P0 price/volume research")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="read-only data and experiment preview")
    plan.add_argument("--config", type=Path, required=True)
    execute = commands.add_parser("run", help="execute or resume the offline P0 pipeline")
    execute.add_argument("--config", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        configure_threads()
        settings = load(args.config)
        if args.command == "plan":
            print(canonical_json_bytes(describe(settings)).decode(), end="")
        else:
            run(settings, args.output, args.resume)
        return 0
    except KeyboardInterrupt:
        print("RESEARCH_INTERRUPTED: rerun the same command with --resume", file=sys.stderr)
        return 130
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
