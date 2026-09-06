from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from xqatexp.research.tables import ResearchBuilder


def _market(security_id: str, factor_date: date, close: Decimal) -> dict[str, object]:
    return {
        "security_id": security_id,
        "trade_date": factor_date,
        "research_close": close,
        "amount_cny": Decimal("10000"),
    }


def test_total_market_value_percentile_is_real_for_complete_cross_section() -> None:
    factor_date = date(2026, 9, 4)
    security_ids = [f"{index:06d}.SZ" for index in range(1, 501)]
    markets = [_market(security_id, factor_date, Decimal("10")) for security_id in security_ids]
    statuses = [
        {
            "security_id": security_id,
            "trade_date": factor_date,
            "is_listed": True,
            "is_st": False,
        }
        for security_id in security_ids
    ]
    raw = {
        "stock_daily_basic": [
            {"ts_code": security_id, "trade_date": "20260904", "total_mv": index}
            for index, security_id in enumerate(security_ids, start=1)
        ]
    }
    masters = {security_id: {"asset_type": "A_SHARE"} for security_id in security_ids}
    rows = ResearchBuilder()._factor_rows(markets, statuses, (), raw, masters, "510300.SH")
    values = {
        row["security_id"]: row["value"] for row in rows if row["factor_id"] == "total_mv_pct_v1"
    }
    assert values[security_ids[0]] == 0.0
    assert values[security_ids[-1]] == 1.0


def test_etf_five_factors_use_rolling_research_prices() -> None:
    start = date(2026, 1, 1)
    markets = [
        _market("510300.SH", start + timedelta(days=index), Decimal(100 + index))
        for index in range(60)
    ]
    rows = ResearchBuilder()._factor_rows(
        markets,
        (),
        (),
        {},
        {"510300.SH": {"asset_type": "CSI300_ETF"}},
        "510300.SH",
    )
    latest = {
        row["factor_id"]: row["value"]
        for row in rows
        if row["factor_date"] == start + timedelta(days=59)
    }
    assert set(latest) == {
        "etf_ma20_v1",
        "etf_ma60_v1",
        "etf_slope60_v1",
        "etf_drawdown60_v1",
        "etf_volatility20_v1",
    }
    assert latest["etf_ma20_v1"] == pytest.approx(sum(range(140, 160)) / 20)
    assert latest["etf_drawdown60_v1"] == pytest.approx(0.0)
