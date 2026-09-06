from __future__ import annotations

import argparse
from pathlib import Path

from xqatexp.demo import create_offline_custom_factor, create_offline_research
from xqatexp.domain.enums import OverwritePolicy


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic XQatExp offline inputs")
    parser.add_argument("--root", type=Path, default=Path(".example-work"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    policy = OverwritePolicy.OVERWRITE if args.overwrite else OverwritePolicy.ERROR
    research = create_offline_research(args.root / "research", policy)
    factor = create_offline_custom_factor(args.root / "custom-factor.csv")
    print(f"OFFLINE_EXAMPLE_READY research={research.path} custom_factor={factor.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
