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
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.registry import get_dataset_by_api
from xqatexp.research.factors import (
    momentum,
    trend_stability,
    volatility,
    volume_price_confirmation,
)
from xqatexp.research.preparation import research_price

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


@dataclass(frozen=True, slots=True)
class ResearchBuildConfig:
    start_date: date
    end_date: date
    csi300_etf_id: str
    existing_policy: OverwritePolicy


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
        if raw_roots:
            raise ValueError("CONFIG_VALUE_INVALID: incremental Raw merge is not yet supported")
        opened = ArtifactReader().open(base)
        base_sha = hashlib.sha256((opened.path / "manifest.json").read_bytes()).hexdigest()
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
        rows["system_factor_daily"] = self._factor_rows(rows["market_daily"])
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
        for market in rows["market_daily"]:
            security_id, trade_date = market["security_id"], market["trade_date"]
            master = masters.get(security_id)
            if master is None or master["asset_type"] != "A_SHARE":
                continue
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
                    "is_suspended_full_day": False,
                    "up_limit": up,
                    "down_limit": down,
                    "is_limit_up_locked": (
                        market["low_raw"] >= up - tolerance if up is not None else None
                    ),
                    "is_limit_down_locked": (
                        market["high_raw"] <= down + tolerance if down is not None else None
                    ),
                    "risk_flags": [] if st_covered else ["ST_COVERAGE_MISSING"],
                    "available_from": trade_date,
                    "source_hash": _source_hash((limit, market)),
                }
            )
        rows["security_status_daily"].sort(
            key=lambda item: (item["security_id"], item["trade_date"])
        )

    def _factor_rows(self, market_rows: Sequence[Row]) -> list[Row]:
        histories: dict[str, list[Row]] = defaultdict(list)
        for row in market_rows:
            histories[str(row["security_id"])].append(row)
        output: list[Row] = []
        for security_id, history in histories.items():
            for index, row in enumerate(history):
                prices = [float(item["research_close"]) for item in history[: index + 1]]
                amounts = [float(item["amount_cny"]) for item in history[: index + 1]]
                definitions = {
                    "momentum_60_ex5_v1": (
                        prices[-6] / prices[-61] - 1.0 if len(prices) >= 61 else None
                    ),
                    "momentum_40_v1": momentum(prices[-41:], 40),
                    "trend_stability_60_v1": trend_stability(prices[-60:])
                    if len(prices) >= 60
                    else None,
                    "volume_price_confirm_20_v1": self._volume_factor(prices, amounts),
                    "volatility_20_v1": volatility(prices[-21:], 20),
                    "roe_annualized_v1": None,
                    "profit_positive_ttm_v1": None,
                    "consecutive_loss_2_v1": None,
                }
                if security_id.endswith((".SH", ".SZ")):
                    for factor_id, value in definitions.items():
                        output.append(
                            self._factor_row(factor_id, security_id, row["trade_date"], value)
                        )
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
