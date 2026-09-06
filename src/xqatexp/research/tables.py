from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from xqatexp import __version__
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublisher, PublishedArtifact
from xqatexp.artifacts.readers import ArtifactReader, ArtifactReadError
from xqatexp.artifacts.schemas import SchemaRegistry, SchemaValidationError
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.registry import get_dataset_by_api
from xqatexp.research.factors import (
    annualized_log_slope,
    average_rank_percentiles,
    momentum,
    trend_stability,
    volatility,
    volume_price_confirmation,
)
from xqatexp.research.preparation import (
    annualized_roe,
    choose_announce_date,
    derive_quarter_profit,
    derive_ttm_profit,
    next_open_date,
    research_price,
)

TABLE_NAMES = (
    "security_master",
    "trade_calendar",
    "market_daily",
    "security_status_daily",
    "financial_snapshot",
    "system_factor_daily",
    "corporate_action",
)
Row = dict[str, Any]
RawMap = dict[str, list[Row]]
InputReference = tuple[str, str, str]
TABLE_KEYS = {
    "security_master": ("security_id",),
    "trade_calendar": ("exchange", "calendar_date"),
    "market_daily": ("security_id", "trade_date"),
    "security_status_daily": ("security_id", "trade_date"),
    "financial_snapshot": ("security_id", "report_period", "announce_date", "revision_seq"),
    "system_factor_daily": ("factor_id", "security_id", "factor_date", "factor_version"),
    "corporate_action": ("event_id",),
}


@dataclass(frozen=True, slots=True)
class ResearchBuildConfig:
    start_date: date
    end_date: date
    csi300_etf_id: str
    existing_policy: OverwritePolicy


@dataclass(frozen=True, slots=True)
class ResearchCheckReport:
    valid: bool
    issue_codes: tuple[str, ...]
    table_rows: dict[str, int]


class ResearchCheckService:
    def __init__(
        self,
        *,
        reader: ArtifactReader | None = None,
        schemas: SchemaRegistry | None = None,
    ) -> None:
        self._reader = reader or ArtifactReader()
        self._schemas = schemas or SchemaRegistry()

    def check(self, path: Path) -> ResearchCheckReport:
        try:
            opened = self._reader.open(path)
            if opened.manifest["artifact_type"] != "RESEARCH_DATA":
                return ResearchCheckReport(False, ("ARTIFACT_SCHEMA_INCOMPATIBLE",), {})
            expected = {f"tables/{name}.parquet" for name in TABLE_NAMES}
            if set(opened.verified_files) != expected:
                return ResearchCheckReport(False, ("DATA_REQUIRED_MISSING",), {})
            tables = {
                name: pq.read_table(opened.path / f"tables/{name}.parquet") for name in TABLE_NAMES
            }
            for name, table in tables.items():
                self._schemas.validate_arrow(name, table)
            issues: set[str] = set()
            security_ids = set(tables["security_master"].column("security_id").to_pylist())
            for name in (
                "market_daily",
                "security_status_daily",
                "financial_snapshot",
                "system_factor_daily",
                "corporate_action",
            ):
                if any(
                    item not in security_ids
                    for item in tables[name].column("security_id").to_pylist()
                ):
                    issues.add("DATA_REQUIRED_MISSING")
            open_dates = {
                row["calendar_date"]
                for row in tables["trade_calendar"].to_pylist()
                if row["is_open"]
            }
            for row in tables["market_daily"].to_pylist():
                if row["trade_date"] not in open_dates:
                    issues.add("DATA_CONFLICT")
            for row in tables["system_factor_daily"].to_pylist():
                if row["input_end_date"] > row["factor_date"]:
                    issues.add("STRATEGY_HISTORY_SLICE_VIOLATION")
                if row["available_from"] > row["factor_date"]:
                    issues.add("DATA_VISIBILITY_UNKNOWN")
            order = (
                "DATA_REQUIRED_MISSING",
                "DATA_CONFLICT",
                "DATA_VISIBILITY_UNKNOWN",
                "STRATEGY_HISTORY_SLICE_VIOLATION",
            )
            codes = tuple(code for code in order if code in issues)
            return ResearchCheckReport(
                not codes,
                codes,
                {name: table.num_rows for name, table in tables.items()},
            )
        except ArtifactReadError as error:
            code = (
                "ARTIFACT_HASH_MISMATCH"
                if "ARTIFACT_HASH_MISMATCH" in str(error)
                else "DATA_INPUT_CORRUPT"
            )
            return ResearchCheckReport(False, (code,), {})
        except (OSError, ValueError, SchemaValidationError, pa.ArrowException):
            return ResearchCheckReport(False, ("DATA_INPUT_CORRUPT",), {})


def _date(value: object | None) -> date | None:
    if value in (None, ""):
        return None
    text = str(value)
    return date.fromisoformat(text if "-" in text else f"{text[:4]}-{text[4:6]}-{text[6:8]}")


def _required_date(value: object) -> date:
    parsed = _date(value)
    if parsed is None:
        raise ValueError("DATA_INPUT_CORRUPT: required date is missing")
    return parsed


def _decimal(value: object | None, quantum: str) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal(quantum), rounding=ROUND_HALF_UP)


def _required_positive_decimal(value: object | None, quantum: str) -> Decimal:
    parsed = _decimal(value, quantum)
    if parsed is None or parsed <= 0:
        raise ValueError("DATA_INPUT_CORRUPT: nonpositive or missing numeric value")
    return parsed


def _facts(record: Row) -> Row:
    return {key: value for key, value in record.items() if not key.startswith("_xqat_")}


def _source_hash(records: Iterable[Row]) -> str:
    payload = b"".join(canonical_json_bytes(_facts(record)) for record in records)
    return hashlib.sha256(payload).hexdigest()


class ResearchBuilder:
    def __init__(
        self,
        *,
        publisher: ArtifactPublisher | None = None,
        schemas: SchemaRegistry | None = None,
    ) -> None:
        self._publisher = publisher or ArtifactPublisher()
        self._schemas = schemas or SchemaRegistry()

    def build(
        self,
        raw_roots: Sequence[Path],
        config: ResearchBuildConfig,
        output: Path,
    ) -> PublishedArtifact:
        if config.start_date > config.end_date:
            raise ValueError("CONFIG_VALUE_INVALID: start_date is after end_date")
        raw, inputs = self._load_raw(raw_roots)
        tables = self._transform(raw, config)
        return self._publish(tables, inputs, config, output)

    def update(
        self,
        base: Path,
        raw_roots: Sequence[Path],
        config: ResearchBuildConfig,
        output: Path,
    ) -> PublishedArtifact:
        opened = ArtifactReader().open(base)
        base_sha = hashlib.sha256((opened.path / "manifest.json").read_bytes()).hexdigest()
        if raw_roots:
            base_tables = {
                name: pq.read_table(opened.path / f"tables/{name}.parquet") for name in TABLE_NAMES
            }
            raw, raw_inputs = self._load_raw(raw_roots)
            delta_tables = self._transform(raw, config)
            merged = {
                name: self._merge_tables(name, base_tables[name], delta_tables[name])
                for name in TABLE_NAMES
                if name != "system_factor_daily"
            }
            masters = {
                str(row["security_id"]): row for row in merged["security_master"].to_pylist()
            }
            open_dates = sorted(
                row["calendar_date"]
                for row in merged["trade_calendar"].to_pylist()
                if row["is_open"]
            )
            generated: dict[str, list[Row]] = {
                "market_daily": delta_tables["market_daily"].to_pylist(),
                "security_status_daily": [],
            }
            self._status_rows(generated, raw, masters, open_dates)
            generated_status = pa.Table.from_pylist(
                generated["security_status_daily"],
                schema=self._schemas.arrow_schema("security_status_daily"),
            )
            merged["security_status_daily"] = self._merge_tables(
                "security_status_daily",
                merged["security_status_daily"],
                generated_status,
            )
            self._normalize_merged_tables(merged)
            recalculated = self._factor_rows(
                merged["market_daily"].to_pylist(),
                merged["security_status_daily"].to_pylist(),
                merged["financial_snapshot"].to_pylist(),
                raw,
                masters,
                config.csi300_etf_id,
            )
            recalculated_table = pa.Table.from_pylist(
                recalculated, schema=self._schemas.arrow_schema("system_factor_daily")
            )
            merged["system_factor_daily"] = self._merge_tables(
                "system_factor_daily",
                base_tables["system_factor_daily"],
                recalculated_table,
                preserve_nonnull=True,
            )
            inputs = (("base", base_sha, base.name), *raw_inputs)
            return self._publish(merged, inputs, config, output)
        now = datetime.now(UTC)

        def build(staging: Path) -> None:
            table_dir = staging / "tables"
            table_dir.mkdir()
            files = []
            for name in TABLE_NAMES:
                source = opened.path / f"tables/{name}.parquet"
                target = table_dir / source.name
                target.write_bytes(source.read_bytes())
                table = pq.read_table(target)
                self._schemas.validate_arrow(name, table)
                payload = target.read_bytes()
                files.append(self._file_entry(name, payload, table.num_rows))
            manifest = self._manifest(
                files,
                (("base", base_sha, base.name),),
                config,
                now,
            )
            (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

        return self._publisher.publish(build, output, config.existing_policy)

    def _normalize_merged_tables(self, tables: dict[str, pa.Table]) -> None:
        calendar = tables["trade_calendar"].to_pylist()
        opens = sorted(row["calendar_date"] for row in calendar if row["is_open"])
        for row in calendar:
            current = row["calendar_date"]
            row["next_trade_date"] = next((item for item in opens if item > current), None)
        tables["trade_calendar"] = pa.Table.from_pylist(
            calendar, schema=self._schemas.arrow_schema("trade_calendar")
        )

        market = tables["market_daily"].to_pylist()
        first_factors: dict[str, Decimal] = {}
        for row in market:
            if row["adj_factor"] is not None:
                first_factors.setdefault(str(row["security_id"]), row["adj_factor"])
        for row in market:
            security_id = str(row["security_id"])
            first = Decimal("1") if security_id == "000300.SH" else first_factors.get(security_id)
            for raw_name, research_name in (
                ("open_raw", "research_open"),
                ("high_raw", "research_high"),
                ("low_raw", "research_low"),
                ("close_raw", "research_close"),
            ):
                row[research_name] = research_price(row[raw_name], row["adj_factor"], first)
        tables["market_daily"] = pa.Table.from_pylist(
            market, schema=self._schemas.arrow_schema("market_daily")
        )

        masters = {str(row["security_id"]): row for row in tables["security_master"].to_pylist()}
        statuses = tables["security_status_daily"].to_pylist()
        for row in statuses:
            listed = masters[str(row["security_id"])]["list_date"]
            row["listing_trade_days"] = sum(
                1 for item in opens if listed <= item <= row["trade_date"]
            )
        tables["security_status_daily"] = pa.Table.from_pylist(
            statuses, schema=self._schemas.arrow_schema("security_status_daily")
        )

    def _merge_tables(
        self,
        name: str,
        base: pa.Table,
        delta: pa.Table,
        *,
        preserve_nonnull: bool = False,
    ) -> pa.Table:
        keys = TABLE_KEYS[name]
        merged: dict[tuple[object, ...], Row] = {
            tuple(row[key] for key in keys): row for row in base.to_pylist()
        }
        for row in delta.to_pylist():
            key = tuple(row[field] for field in keys)
            existing = merged.get(key)
            if (
                preserve_nonnull
                and existing is not None
                and existing.get("value") is not None
                and row.get("value") is None
            ):
                continue
            merged[key] = row
        ordered = [merged[key] for key in sorted(merged)]
        table = pa.Table.from_pylist(ordered, schema=self._schemas.arrow_schema(name))
        self._schemas.validate_arrow(name, table)
        return table

    def _load_raw(self, roots: Sequence[Path]) -> tuple[RawMap, tuple[InputReference, ...]]:
        grouped: RawMap = defaultdict(list)
        inputs = []
        reader = ArtifactReader()
        for index, root in enumerate(roots):
            opened = reader.open(root)
            request = json.loads((opened.path / "request.json").read_text(encoding="utf-8"))
            spec = get_dataset_by_api(str(request["api_name"]))
            with gzip.open(opened.path / "response.jsonl.gz", "rt", encoding="utf-8") as stream:
                grouped[spec.dataset_id].extend(json.loads(line) for line in stream)
            digest = hashlib.sha256((opened.path / "manifest.json").read_bytes()).hexdigest()
            inputs.append((f"raw-{index:04d}", digest, root.name))
        return grouped, tuple(inputs)

    def _transform(self, raw: RawMap, config: ResearchBuildConfig) -> dict[str, pa.Table]:
        rows: dict[str, list[Row]] = {name: [] for name in TABLE_NAMES}
        masters: dict[str, Row] = {}
        for record in raw.get("stock_basic", []):
            fact = _facts(record)
            security_id = str(fact["ts_code"])
            exchange = "SH" if str(fact["exchange"]) == "SSE" else "SZ"
            masters[security_id] = {
                "security_id": security_id,
                "symbol": str(fact["symbol"]),
                "exchange": exchange,
                "asset_type": "A_SHARE",
                "currency": "CNY",
                "list_date": _required_date(fact["list_date"]),
                "delist_date": _date(fact.get("delist_date")),
                "buy_lot_size": 100,
                "sell_lot_size": 100,
                "price_tick": Decimal("0.010000"),
                "rule_effective_from": date(1990, 1, 1),
                "source_hash": _source_hash((record,)),
            }
        for record in raw.get("fund_basic", []):
            fact = _facts(record)
            security_id = str(fact["ts_code"])
            if security_id != config.csi300_etf_id:
                continue
            masters[security_id] = {
                "security_id": security_id,
                "symbol": security_id.split(".", 1)[0],
                "exchange": security_id.split(".", 1)[1],
                "asset_type": "CSI300_ETF",
                "currency": "CNY",
                "list_date": _required_date(fact["list_date"]),
                "delist_date": _date(fact.get("delist_date")),
                "buy_lot_size": 100,
                "sell_lot_size": 100,
                "price_tick": Decimal("0.001000"),
                "rule_effective_from": date(1990, 1, 1),
                "source_hash": _source_hash((record,)),
            }
        if raw.get("index_daily"):
            record = raw["index_daily"][0]
            masters["000300.SH"] = {
                "security_id": "000300.SH",
                "symbol": "000300",
                "exchange": "SH",
                "asset_type": "CSI300_INDEX",
                "currency": "CNY",
                "list_date": min(_required_date(item["trade_date"]) for item in raw["index_daily"]),
                "delist_date": None,
                "buy_lot_size": 1,
                "sell_lot_size": 1,
                "price_tick": Decimal("0.000001"),
                "rule_effective_from": date(1990, 1, 1),
                "source_hash": _source_hash((record,)),
            }
        rows["security_master"] = sorted(masters.values(), key=lambda item: item["security_id"])

        calendar = sorted(raw.get("trade_calendar", []), key=lambda item: str(item["cal_date"]))
        definite_open_dates = [
            _required_date(item["cal_date"]) for item in calendar if int(str(item["is_open"])) == 1
        ]
        for record in calendar:
            fact = _facts(record)
            calendar_date = _required_date(fact["cal_date"])
            following = next((item for item in definite_open_dates if item > calendar_date), None)
            rows["trade_calendar"].append(
                {
                    "exchange": "SH",
                    "calendar_date": calendar_date,
                    "is_open": bool(int(str(fact["is_open"]))),
                    "previous_trade_date": _date(fact.get("pretrade_date")),
                    "next_trade_date": following,
                    "source_hash": _source_hash((record,)),
                }
            )

        adjustments = {
            (str(item["ts_code"]), _required_date(item["trade_date"])): Decimal(
                str(item["adj_factor"])
            )
            for dataset in ("stock_adj_factor", "fund_adj_factor")
            for item in raw.get(dataset, [])
        }
        first_adjustment: dict[str, Decimal] = {}
        for (security_id, _), value in sorted(adjustments.items()):
            first_adjustment.setdefault(security_id, value)
        daily_records = [
            item
            for dataset in ("stock_daily", "fund_daily", "index_daily")
            for item in raw.get(dataset, [])
        ]
        for record in daily_records:
            fact = _facts(record)
            security_id = str(fact["ts_code"])
            trade_date = _required_date(fact["trade_date"])
            factor = (
                Decimal("1")
                if security_id == "000300.SH"
                else adjustments.get((security_id, trade_date))
            )
            first = (
                Decimal("1") if security_id == "000300.SH" else first_adjustment.get(security_id)
            )
            raw_prices = {
                field: _required_positive_decimal(fact.get(field), "0.000001")
                for field in ("open", "high", "low", "close", "pre_close")
            }
            if not (
                raw_prices["low"] <= raw_prices["open"] <= raw_prices["high"]
                and raw_prices["low"] <= raw_prices["close"] <= raw_prices["high"]
            ):
                raise ValueError("DATA_INPUT_CORRUPT: OHLC invariant failed")
            volume = Decimal(str(fact["vol"])) * 100
            if volume != volume.to_integral_value():
                raise ValueError("DATA_INPUT_CORRUPT: fractional normalized volume")
            quality = [] if factor is not None else ["ADJ_FACTOR_MISSING"]
            rows["market_daily"].append(
                {
                    "security_id": security_id,
                    "trade_date": trade_date,
                    "open_raw": raw_prices["open"],
                    "high_raw": raw_prices["high"],
                    "low_raw": raw_prices["low"],
                    "close_raw": raw_prices["close"],
                    "pre_close_raw": raw_prices["pre_close"],
                    "volume_shares": int(volume),
                    "amount_cny": _decimal(Decimal(str(fact["amount"])) * 1000, "0.0001"),
                    "adj_factor": _decimal(factor, "0.000000000001"),
                    "research_open": research_price(raw_prices["open"], factor, first),
                    "research_high": research_price(raw_prices["high"], factor, first),
                    "research_low": research_price(raw_prices["low"], factor, first),
                    "research_close": research_price(raw_prices["close"], factor, first),
                    "available_from": trade_date,
                    "quality_flags": quality,
                }
            )
        rows["market_daily"].sort(key=lambda item: (item["security_id"], item["trade_date"]))
        self._status_rows(rows, raw, masters, definite_open_dates)
        rows["financial_snapshot"] = self._financial_rows(raw, definite_open_dates)
        rows["corporate_action"] = self._corporate_action_rows(
            raw, config.start_date, config.end_date
        )
        rows["system_factor_daily"] = self._factor_rows(
            rows["market_daily"],
            rows["security_status_daily"],
            rows["financial_snapshot"],
            raw,
            masters,
            config.csi300_etf_id,
        )
        return {
            name: pa.Table.from_pylist(rows[name], schema=self._schemas.arrow_schema(name))
            for name in TABLE_NAMES
        }

    def _status_rows(
        self,
        rows: dict[str, list[Row]],
        raw: RawMap,
        masters: dict[str, Row],
        open_dates: Sequence[date],
    ) -> None:
        limits = {
            (str(item["ts_code"]), _required_date(item["trade_date"])): item
            for item in raw.get("stock_price_limit", [])
        }
        st = {
            (str(item["ts_code"]), _required_date(item["trade_date"]))
            for item in raw.get("stock_st_status", [])
        }
        st_covered = "stock_st_status" in raw
        suspension_covered = "stock_suspend" in raw
        suspensions: dict[tuple[str, date], list[Row]] = defaultdict(list)
        for item in raw.get("stock_suspend", []):
            suspensions[(str(item["ts_code"]), _required_date(item["trade_date"]))].append(item)
        market_by_key = {
            (str(item["security_id"]), item["trade_date"]): item
            for item in rows["market_daily"]
            if masters.get(str(item["security_id"]), {}).get("asset_type") == "A_SHARE"
        }
        relevant_dates = sorted(
            {
                *(item[1] for item in market_by_key),
                *(item[1] for item in suspensions),
                *(item[1] for item in st),
                *(item[1] for item in limits),
            }
            & set(open_dates)
        )
        for security_id, master in sorted(masters.items()):
            if master["asset_type"] != "A_SHARE":
                continue
            for trade_date in relevant_dates:
                if trade_date < master["list_date"] or (
                    master["delist_date"] is not None and trade_date > master["delist_date"]
                ):
                    continue
                market = market_by_key.get((security_id, trade_date), {})
                suspension_records = suspensions.get((security_id, trade_date), [])
                full_day = any(
                    not str(item.get("suspend_timing") or "").strip() for item in suspension_records
                )
                intraday = any(
                    str(item.get("suspend_timing") or "").strip() for item in suspension_records
                )
                risk_flags = []
                if not st_covered:
                    risk_flags.append("ST_COVERAGE_MISSING")
                if not suspension_covered:
                    risk_flags.append("SUSPENSION_COVERAGE_MISSING")
                if intraday and not full_day:
                    risk_flags.append("INTRADAY_SUSPENSION_UNSUPPORTED")
                if not market and not full_day:
                    risk_flags.append("MARKET_BAR_MISSING")
                limit = limits.get((security_id, trade_date), {})
                up = _decimal(limit.get("up_limit"), "0.000001")
                down = _decimal(limit.get("down_limit"), "0.000001")
                tolerance = Decimal("0.005")
                rows["security_status_daily"].append(
                    {
                        "security_id": security_id,
                        "trade_date": trade_date,
                        "is_listed": master["list_date"] <= trade_date
                        and (master["delist_date"] is None or trade_date <= master["delist_date"]),
                        "listing_trade_days": sum(
                            1 for item in open_dates if master["list_date"] <= item <= trade_date
                        ),
                        "is_st": ((security_id, trade_date) in st) if st_covered else None,
                        "is_suspended_full_day": (full_day if suspension_covered else None),
                        "up_limit": up,
                        "down_limit": down,
                        "is_limit_up_locked": (
                            market.get("low_raw") is not None
                            and market["low_raw"] >= up - tolerance
                            if up is not None
                            else None
                        ),
                        "is_limit_down_locked": (
                            market.get("high_raw") is not None
                            and market["high_raw"] <= down + tolerance
                            if down is not None
                            else None
                        ),
                        "risk_flags": sorted(risk_flags),
                        "available_from": trade_date,
                        "source_hash": _source_hash((limit, market, *suspension_records)),
                    }
                )
        rows["security_status_daily"].sort(
            key=lambda item: (item["security_id"], item["trade_date"])
        )

    def _financial_rows(self, raw: RawMap, open_dates: Sequence[date]) -> list[Row]:
        incomes = raw.get("income", [])

        def announced(item: Row) -> date | None:
            return choose_announce_date(
                str(item["f_ann_date"]) if item.get("f_ann_date") else None,
                str(item["ann_date"]) if item.get("ann_date") else None,
            )

        def ytd(item: Row | None) -> Decimal | None:
            if item is None or item.get("n_income_attr_p") is None:
                return None
            return _decimal(Decimal(str(item["n_income_attr_p"])) * 10_000, "0.0001")

        def visible_announcement(item: Row, as_of: date) -> bool:
            value = announced(item)
            return value is not None and value <= as_of

        def visible_indicator(item: Row, as_of: date) -> bool:
            value = _date(item.get("ann_date"))
            return value is not None and value <= as_of

        def latest_component(security_id: str, period: date, as_of: date) -> Row | None:
            candidates = [
                item
                for item in incomes
                if str(item["ts_code"]) == security_id
                and _required_date(item["end_date"]) == period
                and visible_announcement(item, as_of)
            ]
            return max(
                candidates,
                key=lambda item: (announced(item), str(item.get("update_flag", ""))),
                default=None,
            )

        output: list[Row] = []
        revisions: dict[tuple[str, date], int] = defaultdict(int)
        for income in sorted(
            incomes,
            key=lambda item: (
                str(item["ts_code"]),
                str(item["end_date"]),
                str(item.get("f_ann_date") or item.get("ann_date")),
                str(item.get("update_flag", "")),
            ),
        ):
            security_id = str(income["ts_code"])
            period = _required_date(income["end_date"])
            announce_date = announced(income)
            if announce_date is None:
                continue
            available = next_open_date(announce_date, open_dates)
            if available is None:
                continue
            key = (security_id, period)
            revisions[key] += 1
            current_ytd = ytd(income)
            quarter = period.month // 3
            previous_quarter_period = (
                date(period.year, (quarter - 1) * 3, 31 if quarter - 1 in {1, 4} else 30)
                if quarter > 1
                else None
            )
            previous_ytd = ytd(
                latest_component(security_id, previous_quarter_period, announce_date)
                if previous_quarter_period is not None
                else None
            )
            previous_full = ytd(
                latest_component(security_id, date(period.year - 1, 12, 31), announce_date)
            )
            previous_same = ytd(
                latest_component(
                    security_id,
                    date(period.year - 1, period.month, period.day),
                    announce_date,
                )
            )
            indicator_candidates = [
                item
                for item in raw.get("fina_indicator", [])
                if str(item["ts_code"]) == security_id
                and _required_date(item["end_date"]) == period
                and visible_indicator(item, announce_date)
            ]
            indicator = max(
                indicator_candidates,
                key=lambda item: (_date(item.get("ann_date")), str(item.get("update_flag", ""))),
                default=None,
            )
            roe = annualized_roe(
                Decimal(str(indicator["roe_yearly"]))
                if indicator is not None and indicator.get("roe_yearly") is not None
                else None
            )
            sources = (income,) if indicator is None else (income, indicator)
            output.append(
                {
                    "security_id": security_id,
                    "report_period": period,
                    "announce_date": announce_date,
                    "available_from": available,
                    "revision_seq": revisions[key],
                    "net_profit_parent_ytd": current_ytd,
                    "net_profit_parent_quarter": derive_quarter_profit(
                        quarter, current_ytd, previous_ytd
                    ),
                    "net_profit_parent_ttm": derive_ttm_profit(
                        quarter, current_ytd, previous_full, previous_same
                    ),
                    "roe_annualized": roe,
                    "consecutive_loss_quarters": None,
                    "source_hash": _source_hash(sources),
                }
            )
        for current in output:
            visible = [
                row
                for row in output
                if row["security_id"] == current["security_id"]
                and row["report_period"] <= current["report_period"]
                and row["available_from"] <= current["available_from"]
            ]
            latest_by_period: dict[date, Row] = {}
            for row in visible:
                period_key = row["report_period"]
                prior = latest_by_period.get(period_key)
                if prior is None or (row["available_from"], row["revision_seq"]) > (
                    prior["available_from"],
                    prior["revision_seq"],
                ):
                    latest_by_period[period_key] = row
            losses = 0
            for row in sorted(
                latest_by_period.values(), key=lambda item: item["report_period"], reverse=True
            ):
                profit = row["net_profit_parent_quarter"]
                if profit is None:
                    losses = -1
                    break
                if profit >= 0:
                    break
                losses += 1
            current["consecutive_loss_quarters"] = None if losses < 0 else losses
        output.sort(
            key=lambda item: (
                item["security_id"],
                item["report_period"],
                item["announce_date"],
                item["revision_seq"],
            )
        )
        return output

    def _corporate_action_rows(self, raw: RawMap, start_date: date, end_date: date) -> list[Row]:
        output = []
        for record in raw.get("dividend", []):
            fact = _facts(record)
            if str(fact.get("div_proc", "")).strip() != "实施":
                continue
            after_tax = _decimal(fact.get("cash_div"), "0.000001")
            before_tax = _decimal(fact.get("cash_div_tax"), "0.000001")
            reported_total = Decimal(str(fact.get("stk_div") or 0))
            component_total = sum(
                (Decimal(str(fact.get(name) or 0)) for name in ("stk_bo_rate", "stk_co_rate")),
                Decimal("0"),
            )
            if (
                reported_total
                and component_total
                and abs(reported_total - component_total) > Decimal("1e-12")
            ):
                raise ValueError("DATA_CONFLICT: inconsistent dividend stock ratios")
            stock_ratio = reported_total if reported_total else component_total
            has_cash = after_tax is not None and after_tax > 0
            action_type = "STOCK_DISTRIBUTION" if stock_ratio > 0 else "CASH_DIVIDEND"
            announce_date = _required_date(fact["ann_date"])
            record_date = _date(fact.get("record_date"))
            ex_date = _date(fact.get("ex_date"))
            pay_date = _date(fact.get("pay_date"))
            stock_list_date = _date(fact.get("div_listdate"))
            if record_date is None or ex_date is None:
                raise ValueError("DATA_REQUIRED_MISSING: corporate action dates")
            if has_cash and pay_date is None:
                raise ValueError("DATA_REQUIRED_MISSING: cash dividend pay_date")
            if stock_ratio > 0 and stock_list_date is None:
                raise ValueError("DATA_REQUIRED_MISSING: stock distribution list date")
            event_dates = tuple(
                item
                for item in (record_date, ex_date, pay_date, stock_list_date)
                if item is not None
            )
            if not any(start_date <= item <= end_date for item in event_dates):
                continue
            event_id = hashlib.sha256(canonical_json_bytes(fact)).hexdigest()
            implementation_date = _date(fact.get("imp_ann_date"))
            output.append(
                {
                    "event_id": event_id,
                    "security_id": str(fact["ts_code"]),
                    "action_type": action_type,
                    "announce_date": announce_date,
                    "implementation_announce_date": implementation_date,
                    "record_date": record_date,
                    "ex_date": ex_date,
                    "pay_date": pay_date,
                    "stock_list_date": stock_list_date,
                    "cash_per_share_before_tax": before_tax,
                    "cash_per_share_after_tax": after_tax,
                    "stock_ratio": _decimal(stock_ratio, "0.000000000001"),
                    "split_ratio": None,
                    "rights_ratio": None,
                    "rights_price": None,
                    "available_from": implementation_date or announce_date,
                    "source_hash": _source_hash((record,)),
                }
            )
        return sorted(output, key=lambda item: item["event_id"])

    def _factor_rows(
        self,
        market_rows: Sequence[Row],
        status_rows: Sequence[Row],
        financial_rows: Sequence[Row],
        raw: RawMap,
        masters: dict[str, Row],
        etf_id: str,
    ) -> list[Row]:
        histories: dict[str, list[Row]] = defaultdict(list)
        for row in market_rows:
            histories[str(row["security_id"])].append(row)
        stock_ids = {
            security_id
            for security_id, master in masters.items()
            if master["asset_type"] == "A_SHARE"
        }
        eligible = {
            (str(row["security_id"]), row["trade_date"])
            for row in status_rows
            if row["is_listed"] is True and row["is_st"] is False
        }
        total_mv = {
            (str(item["ts_code"]), _required_date(item["trade_date"])): float(item["total_mv"])
            for item in raw.get("stock_daily_basic", [])
            if item.get("total_mv") is not None
        }
        amount_means: dict[tuple[str, date], float] = {}
        for security_id in stock_ids:
            history = histories.get(security_id, [])
            for index, row in enumerate(history):
                if index >= 19:
                    amount_means[(security_id, row["trade_date"])] = (
                        sum(float(item["amount_cny"]) for item in history[index - 19 : index + 1])
                        / 20.0
                    )
        mv_percentiles = self._daily_percentiles(total_mv, eligible)
        amount_percentiles = self._daily_percentiles(amount_means, eligible)
        output: list[Row] = []
        for security_id, history in histories.items():
            for index, row in enumerate(history):
                prices = [float(item["research_close"]) for item in history[: index + 1]]
                amounts = [float(item["amount_cny"]) for item in history[: index + 1]]
                factor_date = row["trade_date"]
                if security_id in stock_ids:
                    latest_financial = self._latest_financial(
                        financial_rows, security_id, factor_date
                    )
                    definitions = {
                        "total_mv_pct_v1": mv_percentiles.get((security_id, factor_date)),
                        "amount_20d_pct_v1": amount_percentiles.get((security_id, factor_date)),
                        "momentum_60_ex5_v1": (
                            prices[-6] / prices[-61] - 1.0 if len(prices) >= 61 else None
                        ),
                        "momentum_40_v1": momentum(prices[-41:], 40),
                        "trend_stability_60_v1": trend_stability(prices[-60:])
                        if len(prices) >= 60
                        else None,
                        "volume_price_confirm_20_v1": self._volume_factor(prices, amounts),
                        "volatility_20_v1": volatility(prices[-21:], 20),
                        "roe_annualized_v1": self._financial_float(
                            latest_financial, "roe_annualized"
                        ),
                        "profit_positive_ttm_v1": self._positive_ttm(latest_financial),
                        "consecutive_loss_2_v1": self._loss_two(latest_financial),
                    }
                    for factor_id, value in definitions.items():
                        output.append(self._factor_row(factor_id, security_id, factor_date, value))
                elif security_id == etf_id:
                    etf_definitions = {
                        "etf_ma20_v1": sum(prices[-20:]) / 20.0 if len(prices) >= 20 else None,
                        "etf_ma60_v1": sum(prices[-60:]) / 60.0 if len(prices) >= 60 else None,
                        "etf_slope60_v1": annualized_log_slope(prices[-60:])
                        if len(prices) >= 60
                        else None,
                        "etf_drawdown60_v1": prices[-1] / max(prices[-60:]) - 1.0
                        if len(prices) >= 60
                        else None,
                        "etf_volatility20_v1": volatility(prices[-21:], 20),
                    }
                    for factor_id, value in etf_definitions.items():
                        output.append(self._factor_row(factor_id, security_id, factor_date, value))
        output.sort(
            key=lambda item: (
                item["factor_id"],
                item["security_id"],
                item["factor_date"],
                item["factor_version"],
            )
        )
        return output

    @staticmethod
    def _daily_percentiles(
        values: dict[tuple[str, date], float], eligible: set[tuple[str, date]]
    ) -> dict[tuple[str, date], float]:
        by_date: dict[date, list[tuple[str, float]]] = defaultdict(list)
        for key, value in values.items():
            if key in eligible:
                by_date[key[1]].append((key[0], value))
        output = {}
        for factor_date, observations in by_date.items():
            if len(observations) < 500:
                continue
            percentiles = average_rank_percentiles([value for _, value in observations])
            for (security_id, _), percentile in zip(observations, percentiles, strict=True):
                if percentile is not None:
                    output[(security_id, factor_date)] = percentile
        return output

    @staticmethod
    def _latest_financial(rows: Sequence[Row], security_id: str, factor_date: date) -> Row | None:
        visible = [
            row
            for row in rows
            if row["security_id"] == security_id and row["available_from"] <= factor_date
        ]
        return max(
            visible,
            key=lambda row: (row["report_period"], row["available_from"], row["revision_seq"]),
            default=None,
        )

    @staticmethod
    def _financial_float(row: Row | None, field: str) -> float | None:
        return None if row is None or row[field] is None else float(row[field])

    @staticmethod
    def _positive_ttm(row: Row | None) -> float | None:
        return (
            None
            if row is None or row["net_profit_parent_ttm"] is None
            else float(row["net_profit_parent_ttm"] > 0)
        )

    @staticmethod
    def _loss_two(row: Row | None) -> float | None:
        return (
            None
            if row is None or row["consecutive_loss_quarters"] is None
            else float(row["consecutive_loss_quarters"] >= 2)
        )

    @staticmethod
    def _volume_factor(prices: list[float], amounts: list[float]) -> float | None:
        if len(prices) < 21:
            return None
        recent_prices, recent_amounts = prices[-21:], amounts[-20:]
        returns = [recent_prices[i] / recent_prices[i - 1] - 1.0 for i in range(1, 21)]
        return volume_price_confirmation(returns, recent_amounts)

    @staticmethod
    def _factor_row(
        factor_id: str, security_id: str, factor_date: date, value: float | None
    ) -> Row:
        return {
            "factor_id": factor_id,
            "factor_version": "1.0.0",
            "security_id": security_id,
            "factor_date": factor_date,
            "value": value,
            "available_from": factor_date,
            "input_start_date": None,
            "input_end_date": factor_date,
            "quality_flags": [] if value is not None else ["INSUFFICIENT_HISTORY"],
        }

    def _publish(
        self,
        tables: dict[str, pa.Table],
        inputs: Sequence[InputReference],
        config: ResearchBuildConfig,
        output: Path,
    ) -> PublishedArtifact:
        now = datetime.now(UTC)

        def build(staging: Path) -> None:
            table_dir = staging / "tables"
            table_dir.mkdir()
            files: list[Row] = []
            for name in TABLE_NAMES:
                table = tables[name]
                self._schemas.validate_arrow(name, table)
                path = table_dir / f"{name}.parquet"
                pq.write_table(table, path, compression="zstd")
                payload = path.read_bytes()
                files.append(self._file_entry(name, payload, table.num_rows))
            manifest = self._manifest(files, inputs, config, now)
            (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

        return self._publisher.publish(build, output, config.existing_policy)

    @staticmethod
    def _file_entry(name: str, payload: bytes, row_count: int) -> Row:
        return {
            "path": f"tables/{name}.parquet",
            "media_type": "application/vnd.apache.parquet",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "row_count": row_count,
            "schema_id": "research_tables",
            "schema_version": "1.0",
        }

    @staticmethod
    def _manifest(
        files: Sequence[Row],
        inputs: Sequence[InputReference],
        config: ResearchBuildConfig,
        now: datetime,
    ) -> Row:
        return {
            "schema_version": "1.0",
            "artifact_type": "RESEARCH_DATA",
            "artifact_id": str(uuid.uuid4()),
            "created_at": now,
            "producer": {"name": "xqatexp", "version": __version__},
            "run": None,
            "inputs": [
                {
                    "alias": alias,
                    "artifact_type": "RAW_DATA" if alias.startswith("raw-") else "RESEARCH_DATA",
                    "manifest_sha256": digest,
                    "path_hint": hint,
                }
                for alias, digest, hint in inputs
            ],
            "date_scope": {"start": config.start_date, "end": config.end_date},
            "files": sorted(files, key=lambda item: str(item["path"])),
            "issues": [],
            "limitations": [],
        }
