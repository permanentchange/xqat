from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from xqatexp import __version__
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublisher, PublishedArtifact
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.research.tables import TABLE_NAMES
from xqatexp.strategy.declaration import ETF_FACTOR_IDS, STOCK_FACTOR_IDS


def create_offline_research(
    output: Path, overwrite: OverwritePolicy = OverwritePolicy.ERROR
) -> PublishedArtifact:
    schemas = SchemaRegistry()
    days = _days(date(2026, 9, 8), 322)
    stocks = tuple(f"600{index:03d}.SH" for index in range(20))
    all_ids = tuple(sorted(("000300.SH", *stocks, "510300.SH")))
    master = []
    for security_id in all_ids:
        if security_id == "000300.SH":
            asset_type, lot = "CSI300_INDEX", 1
        elif security_id == "510300.SH":
            asset_type, lot = "CSI300_ETF", 100
        else:
            asset_type, lot = "A_SHARE", 100
        master.append(
            {
                "security_id": security_id,
                "symbol": security_id[:6],
                "exchange": "SSE",
                "asset_type": asset_type,
                "currency": "CNY",
                "list_date": date(2020, 1, 1),
                "delist_date": None,
                "buy_lot_size": lot,
                "sell_lot_size": lot,
                "price_tick": Decimal("0.010000"),
                "rule_effective_from": date(2020, 1, 1),
                "source_hash": "0" * 64,
            }
        )
    calendar = [
        {
            "exchange": "SSE",
            "calendar_date": day,
            "is_open": True,
            "previous_trade_date": days[index - 1] if index else None,
            "next_trade_date": days[index + 1] if index + 1 < len(days) else None,
            "source_hash": "0" * 64,
        }
        for index, day in enumerate(days)
    ]
    market = []
    for security_number, security_id in enumerate(all_ids):
        base = Decimal("100") if security_id in {"000300.SH", "510300.SH"} else Decimal("10")
        for index, day in enumerate(days):
            close = base + Decimal(index) / Decimal("100") + Decimal(security_number) / 100
            market.append(
                {
                    "security_id": security_id,
                    "trade_date": day,
                    "open_raw": close,
                    "high_raw": close + Decimal("0.10"),
                    "low_raw": close - Decimal("0.10"),
                    "close_raw": close,
                    "pre_close_raw": close - Decimal("0.01"),
                    "volume_shares": 10_000_000,
                    "amount_cny": close * 10_000_000,
                    "adj_factor": Decimal("1"),
                    "research_open": close,
                    "research_high": close + Decimal("0.10"),
                    "research_low": close - Decimal("0.10"),
                    "research_close": close,
                    "available_from": day,
                    "quality_flags": [],
                }
            )
    status = []
    for stock_index, security_id in enumerate(stocks):
        for day_index, day in enumerate(days):
            close = Decimal("10") + Decimal(day_index) / 100 + Decimal(stock_index + 1) / 100
            status.append(
                {
                    "security_id": security_id,
                    "trade_date": day,
                    "is_listed": True,
                    "listing_trade_days": 500 + day_index,
                    "is_st": False,
                    "is_suspended_full_day": False,
                    "up_limit": close + Decimal("1"),
                    "down_limit": close - Decimal("1"),
                    "is_limit_up_locked": False,
                    "is_limit_down_locked": False,
                    "risk_flags": [],
                    "available_from": day,
                    "source_hash": "0" * 64,
                }
            )
    factors = []
    for factor_id in STOCK_FACTOR_IDS:
        for stock_index, security_id in enumerate(stocks):
            for day in days:
                if factor_id in {"total_mv_pct_v1", "amount_20d_pct_v1"}:
                    value = 0.9
                elif factor_id == "profit_positive_ttm_v1":
                    value = 1.0
                elif factor_id == "consecutive_loss_2_v1":
                    value = 0.0
                elif factor_id == "volatility_20_v1":
                    value = float(20 - stock_index)
                else:
                    value = float(stock_index + 1)
                factors.append(_factor(factor_id, security_id, day, value))
    for factor_id in ETF_FACTOR_IDS:
        for day_index, day in enumerate(days):
            etf_close = 100.0 + day_index / 100
            values: dict[str, float] = {
                "etf_ma20_v1": etf_close - 1.0,
                "etf_ma60_v1": etf_close - 2.0,
                "etf_slope60_v1": 0.1,
                "etf_drawdown60_v1": -0.01,
                "etf_volatility20_v1": 0.2,
            }
            factors.append(_factor(factor_id, "510300.SH", day, values[factor_id]))
    factors.sort(
        key=lambda row: (
            row["factor_id"],
            row["security_id"],
            row["factor_date"],
            row["factor_version"],
        )
    )
    rows = {
        "security_master": master,
        "trade_calendar": calendar,
        "market_daily": market,
        "security_status_daily": status,
        "financial_snapshot": [],
        "system_factor_daily": factors,
        "corporate_action": [],
    }

    def build(staging: Path) -> None:
        table_dir = staging / "tables"
        table_dir.mkdir()
        files = []
        for name in TABLE_NAMES:
            table = pa.Table.from_pylist(rows[name], schema=schemas.arrow_schema(name))
            schemas.validate_arrow(name, table)
            member = table_dir / f"{name}.parquet"
            pq.write_table(table, member, compression="zstd")
            payload = member.read_bytes()
            files.append(
                {
                    "path": f"tables/{name}.parquet",
                    "media_type": "application/vnd.apache.parquet",
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "row_count": table.num_rows,
                    "schema_id": "research_tables",
                    "schema_version": "1.0",
                }
            )
        manifest = {
            "schema_version": "1.0",
            "artifact_type": "RESEARCH_DATA",
            "artifact_id": "offline-example-research-v1",
            "created_at": datetime(2026, 9, 4, tzinfo=UTC),
            "producer": {"name": "xqatexp", "version": __version__},
            "run": {"mode": "OFFLINE_EXAMPLE"},
            "inputs": [],
            "date_scope": {"start": days[0], "end": days[-1]},
            "files": files,
            "issues": [],
            "limitations": ["SYNTHETIC_OFFLINE_EXAMPLE"],
        }
        (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

    return ArtifactPublisher().publish(build, output, overwrite)


def create_offline_custom_factor(output: Path) -> Path:
    days = _days(date(2026, 9, 8), 322)
    stocks = tuple(f"600{index:03d}.SH" for index in range(20))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("factor_name", "security_id", "factor_date", "factor_value"))
    for security_index, security_id in enumerate(stocks):
        for day in days:
            writer.writerow(("quality_score", security_id, day.isoformat(), security_index + 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(stream.getvalue(), encoding="utf-8", newline="")
    return output


def _days(end: date, count: int) -> tuple[date, ...]:
    values: list[date] = []
    current = end
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(values))


def _factor(factor_id: str, security_id: str, day: date, value: float) -> dict[str, object]:
    return {
        "factor_id": factor_id,
        "factor_version": "1.0.0",
        "security_id": security_id,
        "factor_date": day,
        "value": value,
        "available_from": day,
        "input_start_date": None,
        "input_end_date": day,
        "quality_flags": [],
    }
