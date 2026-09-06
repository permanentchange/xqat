from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-tushare",
        action="store_true",
        default=False,
        help="allow the explicitly marked low-call Tushare smoke test",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live-tushare"):
        return
    marker = pytest.mark.skip(reason="live Tushare requires explicit --live-tushare")
    for item in items:
        if "live_tushare" in item.keywords:
            item.add_marker(marker)
