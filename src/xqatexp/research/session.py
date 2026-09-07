from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any, cast

import duckdb

from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.domain.contracts import CustomFactorView, SecuritySnapshot, StrategyDeclaration
from xqatexp.domain.enums import AssetType
from xqatexp.research.tables import TABLE_NAMES, ResearchCheckService


class ResearchAccessError(ValueError):
    """A restricted research view rejected an unsafe or unavailable query."""


_MINIMUM_TEMP_FREE_BYTES = 1024**3


def _remove_session_temp(path: Path, parent: Path) -> None:
    """Remove exactly one session-owned temporary directory."""
    if path.parent != parent or not path.name.startswith(".xqatexp-tmp-"):
        raise ResearchAccessError("ARTIFACT_TEMP_CLEANUP_FAILED: unsafe temporary path")
    if path.exists():
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise ResearchAccessError(f"ARTIFACT_TEMP_CLEANUP_FAILED: {path.name}") from exc


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


def _count(cursor: duckdb.DuckDBPyConnection) -> int:
    row = cursor.fetchone()
    return 0 if row is None else int(row[0])


class ResearchSession:
    def __init__(
        self,
        path: Path,
        declaration: StrategyDeclaration,
        *,
        temporary_parent: Path | None = None,
    ) -> None:
        report = ResearchCheckService().check(path)
        if not report.valid:
            raise ResearchAccessError(f"DATA_INPUT_CORRUPT: {','.join(report.issue_codes)}")
        opened = ArtifactReader().open(path)
        parent = (temporary_parent or path.parent).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(parent).free < _MINIMUM_TEMP_FREE_BYTES:
            raise ResearchAccessError(
                "ARTIFACT_RESOURCE_INSUFFICIENT: temporary storage has less than 1GB free"
            )
        self._temporary_parent = parent
        self._temporary_directory = Path(
            tempfile.mkdtemp(prefix=".xqatexp-tmp-", dir=parent)
        ).resolve()
        connection: duckdb.DuckDBPyConnection | None = None
        try:
            connection = duckdb.connect(":memory:")
            self._connection = connection
            self._connection.execute("SET memory_limit='1GB'")
            self._connection.execute(f"SET threads={max(1, min(4, os.cpu_count() or 1))}")
            escaped_temp = self._temporary_directory.as_posix().replace("'", "''")
            self._connection.execute(f"SET temp_directory='{escaped_temp}'")
            for name in TABLE_NAMES:
                relation = self._connection.read_parquet(
                    str(opened.path / f"tables/{name}.parquet")
                )
                relation.create_view(name)
        except BaseException:
            if connection is not None:
                with suppress(BaseException):
                    connection.close()
            with suppress(ResearchAccessError):
                _remove_session_temp(self._temporary_directory, self._temporary_parent)
            raise
        self._declaration = declaration
        self.closed = False

    def __enter__(self) -> ResearchSession:
        return self

    def __exit__(
        self,
        _exc_type: object,
        exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        if exc_value is None:
            self.close()
        else:
            with suppress(BaseException):
                self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        close_error: BaseException | None = None
        try:
            self._connection.close()
        except BaseException as exc:
            close_error = exc
        try:
            _remove_session_temp(self._temporary_directory, self._temporary_parent)
        except ResearchAccessError:
            if close_error is None:
                raise
        if close_error is not None:
            raise close_error

    def view(self, decision_date: date) -> ResearchDataViewImpl:
        if self.closed:
            raise ResearchAccessError("DATA_INPUT_CORRUPT: session is closed")
        days = [
            row[0]
            for row in self._connection.execute(
                "SELECT calendar_date FROM trade_calendar "
                "WHERE is_open AND calendar_date <= ? ORDER BY calendar_date",
                [decision_date],
            ).fetchall()
        ]
        if not days or days[-1] != decision_date:
            raise ResearchAccessError("DATA_REQUIRED_MISSING: decision date is not open")
        scoped = days[-self._declaration.lookback_trade_days :]
        return ResearchDataViewImpl(self._connection, self._declaration, decision_date, scoped[0])

    def trading_days(self, start: date, end: date) -> tuple[date, ...]:
        if self.closed or start > end:
            raise ResearchAccessError("DATA_INPUT_CORRUPT: invalid research session range")
        return tuple(
            cast(date, row[0])
            for row in self._connection.execute(
                "SELECT calendar_date FROM trade_calendar "
                "WHERE is_open AND calendar_date BETWEEN ? AND ? ORDER BY calendar_date",
                [start, end],
            ).fetchall()
        )

    def next_trading_day(self, after: date) -> date:
        if self.closed:
            raise ResearchAccessError("DATA_INPUT_CORRUPT: session is closed")
        row = self._connection.execute(
            "SELECT calendar_date FROM trade_calendar "
            "WHERE is_open AND calendar_date>? ORDER BY calendar_date LIMIT 1",
            [after],
        ).fetchone()
        if row is None:
            raise ResearchAccessError("DATA_REQUIRED_MISSING: next trading day")
        return cast(date, row[0])

    def execution_rows(
        self, execution_date: date, security_ids: Sequence[str]
    ) -> tuple[dict[str, Any], ...]:
        if self.closed:
            raise ResearchAccessError("DATA_INPUT_CORRUPT: session is closed")
        if not security_ids:
            return ()
        marks = ",".join("?" for _ in security_ids)
        return tuple(
            _rows(
                self._connection.execute(
                    "SELECT m.security_id,m.asset_type,m.price_tick,m.buy_lot_size,"
                    "m.sell_lot_size,d.open_raw,d.high_raw,d.low_raw,d.close_raw,"
                    "COALESCE(d.volume_shares,0) AS volume_shares,"
                    "v.close_raw AS valuation_close,s.is_listed,s.is_suspended_full_day,s.up_limit,"
                    "s.down_limit,s.is_limit_up_locked,s.is_limit_down_locked "
                    "FROM security_master m LEFT JOIN market_daily d ON "
                    "d.security_id=m.security_id AND d.trade_date=? AND d.available_from<=? "
                    "LEFT JOIN security_status_daily s ON s.security_id=m.security_id "
                    "AND s.trade_date=? AND s.available_from<=? "
                    "LEFT JOIN LATERAL (SELECT close_raw FROM market_daily p "
                    "WHERE p.security_id=m.security_id AND p.trade_date<=? "
                    "AND p.available_from<=? AND p.close_raw IS NOT NULL "
                    "ORDER BY p.trade_date DESC LIMIT 1) v ON true "
                    f"WHERE m.security_id IN ({marks}) ORDER BY m.security_id",
                    [
                        execution_date,
                        execution_date,
                        execution_date,
                        execution_date,
                        execution_date,
                        execution_date,
                        *security_ids,
                    ],
                )
            )
        )

    def corporate_actions(self, start: date, end: date) -> tuple[dict[str, Any], ...]:
        """Return execution-only events whose account dates intersect the range."""
        if self.closed or start > end:
            raise ResearchAccessError("DATA_INPUT_CORRUPT: invalid corporate action range")
        return tuple(
            _rows(
                self._connection.execute(
                    "SELECT event_id,security_id,action_type,record_date,ex_date,pay_date,"
                    "stock_list_date,cash_per_share_before_tax,cash_per_share_after_tax,"
                    "stock_ratio,split_ratio,rights_ratio,rights_price "
                    "FROM corporate_action WHERE "
                    "(record_date BETWEEN ? AND ?) OR (ex_date BETWEEN ? AND ?) OR "
                    "(pay_date BETWEEN ? AND ?) OR (stock_list_date BETWEEN ? AND ?) "
                    "ORDER BY COALESCE(record_date,ex_date,pay_date,stock_list_date),event_id",
                    [start, end, start, end, start, end, start, end],
                )
            )
        )

    def benchmark_close(self, on_date: date) -> Any:
        if self.closed:
            raise ResearchAccessError("DATA_INPUT_CORRUPT: session is closed")
        row = self._connection.execute(
            "SELECT close_raw FROM market_daily WHERE security_id='000300.SH' "
            "AND trade_date=? AND available_from<=?",
            [on_date, on_date],
        ).fetchone()
        if row is None or row[0] is None:
            raise ResearchAccessError(f"BACKTEST_VALUATION_MISSING: benchmark on {on_date}")
        return row[0]


class ResearchDataViewImpl:
    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        declaration: StrategyDeclaration,
        decision_date: date,
        earliest_date: date,
    ) -> None:
        self._connection = connection
        self._declaration = declaration
        self.decision_date = decision_date
        self.earliest_date = earliest_date

    def trading_days(self, start: date, end: date) -> tuple[date, ...]:
        self._bounded(start, end)
        return tuple(
            row[0]
            for row in self._connection.execute(
                "SELECT calendar_date FROM trade_calendar "
                "WHERE is_open AND calendar_date BETWEEN ? AND ? ORDER BY calendar_date",
                [start, end],
            ).fetchall()
        )

    def slice(self, as_of_date: date) -> ResearchDataSliceImpl:
        self._bounded(as_of_date, as_of_date)
        return ResearchDataSliceImpl(
            self._connection, self._declaration, as_of_date, self.earliest_date
        )

    def next_trading_day(self, after: date) -> date:
        row = self._connection.execute(
            "SELECT calendar_date FROM trade_calendar "
            "WHERE is_open AND calendar_date>? ORDER BY calendar_date LIMIT 1",
            [after],
        ).fetchone()
        if row is None:
            raise ResearchAccessError("DATA_REQUIRED_MISSING: next trading day")
        return cast(date, row[0])

    def readiness_coverage(self, custom_factors: CustomFactorView | None) -> dict[str, float]:
        days = self.trading_days(self.earliest_date, self.decision_date)[-252:]
        latest_by_week: dict[tuple[int, int], date] = {}
        for day in days:
            iso = day.isocalendar()
            latest_by_week[(iso.year, iso.week)] = day
        decision_days = tuple(sorted(latest_by_week.values()))
        stock_factors = tuple(
            item
            for item in self._declaration.required_system_factors
            if not item.startswith("etf_")
        )
        etf_factors = tuple(
            item for item in self._declaration.required_system_factors if item.startswith("etf_")
        )
        samples: dict[str, list[float]] = {
            "market_status": [],
            "financial": [],
            "system_factors": [],
            "etf_factors": [],
            "custom_factors": [],
        }
        for decision_day in decision_days:
            active = tuple(
                str(row[0])
                for row in self._connection.execute(
                    "SELECT security_id FROM security_master WHERE asset_type='A_SHARE' "
                    "AND list_date<=? AND (delist_date IS NULL OR delist_date>=?) "
                    "ORDER BY security_id",
                    [decision_day, decision_day],
                ).fetchall()
            )
            denominator = len(active)
            if not active:
                for key in ("market_status", "financial", "system_factors"):
                    samples[key].append(0.0)
            else:
                marks = ",".join("?" for _ in active)
                market_status = _count(
                    self._connection.execute(
                        "SELECT COUNT(*) FROM security_status_daily s WHERE "
                        f"s.security_id IN ({marks}) AND s.trade_date=? "
                        "AND s.available_from<=? AND s.is_st IS NOT NULL "
                        "AND s.is_suspended_full_day IS NOT NULL AND "
                        "(s.is_suspended_full_day OR EXISTS (SELECT 1 FROM market_daily d "
                        "WHERE d.security_id=s.security_id AND d.trade_date=s.trade_date "
                        "AND d.available_from<=?))",
                        [*active, decision_day, decision_day, decision_day],
                    )
                )
                financial = _count(
                    self._connection.execute(
                        "SELECT COUNT(*) FROM security_master m WHERE "
                        f"m.security_id IN ({marks}) AND EXISTS "
                        "(SELECT 1 FROM financial_snapshot f "
                        "WHERE f.security_id=m.security_id AND f.available_from<=?)",
                        [*active, decision_day],
                    )
                )
                samples["market_status"].append(float(market_status) / denominator)
                samples["financial"].append(float(financial) / denominator)
                if stock_factors:
                    factor_marks = ",".join("?" for _ in stock_factors)
                    observed = _count(
                        self._connection.execute(
                            "SELECT COUNT(*) FROM system_factor_daily WHERE "
                            f"factor_id IN ({factor_marks}) AND security_id IN ({marks}) "
                            "AND factor_date=? AND available_from<=?",
                            [*stock_factors, *active, decision_day, decision_day],
                        )
                    )
                    samples["system_factors"].append(
                        float(observed) / (denominator * len(stock_factors))
                    )
                if self._declaration.required_custom_factors and custom_factors is not None:
                    loaded = cast(
                        Mapping[str, Mapping[str, float]],
                        custom_factors.values_at(
                            decision_day,
                            self._declaration.required_custom_factors,
                            active,
                        ),
                    )
                    observed = sum(len(loaded.get(name, {})) for name in loaded)
                    samples["custom_factors"].append(
                        observed / (denominator * len(self._declaration.required_custom_factors))
                    )
            if etf_factors:
                factor_marks = ",".join("?" for _ in etf_factors)
                observed = _count(
                    self._connection.execute(
                        "SELECT COUNT(DISTINCT factor_id) FROM system_factor_daily WHERE "
                        f"factor_id IN ({factor_marks}) AND factor_date=? AND available_from<=?",
                        [*etf_factors, decision_day, decision_day],
                    )
                )
                samples["etf_factors"].append(float(observed) / len(etf_factors))
        return {key: min(values) if values else 1.0 for key, values in samples.items()}

    def _bounded(self, start: date, end: date) -> None:
        if start < self.earliest_date or end > self.decision_date or start > end:
            raise ResearchAccessError("STRATEGY_HISTORY_SLICE_VIOLATION: date outside view")


class ResearchDataSliceImpl:
    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        declaration: StrategyDeclaration,
        as_of_date: date,
        earliest_date: date,
    ) -> None:
        self._connection: duckdb.DuckDBPyConnection = connection
        self._declaration: StrategyDeclaration = declaration
        self.as_of_date: date = as_of_date
        self._earliest_date: date = earliest_date

    def universe(self) -> tuple[SecuritySnapshot, ...]:
        rows = _rows(
            self._connection.execute(
                "SELECT m.security_id,m.asset_type,s.is_listed,s.listing_trade_days,s.is_st,"
                "s.is_suspended_full_day,s.risk_flags,f.net_profit_parent_ttm,"
                "f.roe_annualized,f.consecutive_loss_quarters FROM security_master m "
                "JOIN security_status_daily s USING(security_id) "
                "LEFT JOIN LATERAL (SELECT net_profit_parent_ttm,roe_annualized,"
                "consecutive_loss_quarters FROM financial_snapshot f "
                "WHERE f.security_id=m.security_id AND f.available_from<=? "
                "ORDER BY f.report_period DESC,f.available_from DESC,f.revision_seq DESC "
                "LIMIT 1) f ON true "
                "WHERE s.trade_date=? AND s.available_from<=? ORDER BY m.security_id",
                [self.as_of_date, self.as_of_date, self.as_of_date],
            )
        )
        return tuple(
            SecuritySnapshot(row["security_id"], AssetType(row.pop("asset_type")), dict(row))
            for row in rows
        )

    def history(
        self,
        security_ids: Sequence[str],
        fields: Sequence[str],
        start: date,
        end: date,
    ) -> tuple[dict[str, Any], ...]:
        self._validate_request(fields, start, end)
        if not security_ids:
            return ()
        columns = ",".join(fields)
        placeholders = ",".join("?" for _ in security_ids)
        sql = (
            f"SELECT security_id,trade_date,{columns} FROM market_daily "
            f"WHERE security_id IN ({placeholders}) AND trade_date BETWEEN ? AND ? "
            "AND available_from<=? ORDER BY security_id,trade_date"
        )
        return tuple(
            _rows(self._connection.execute(sql, [*security_ids, start, end, self.as_of_date]))
        )

    def system_factors(
        self, factor_ids: Sequence[str], security_ids: Sequence[str]
    ) -> tuple[dict[str, Any], ...]:
        if not set(factor_ids).issubset(self._declaration.required_system_factors):
            raise ResearchAccessError("STRATEGY_HISTORY_SLICE_VIOLATION: undeclared factor")
        if not factor_ids or not security_ids:
            return ()
        factor_marks = ",".join("?" for _ in factor_ids)
        security_marks = ",".join("?" for _ in security_ids)
        return tuple(
            _rows(
                self._connection.execute(
                    "SELECT factor_id,security_id,factor_date,value,quality_flags "
                    f"FROM system_factor_daily WHERE factor_id IN ({factor_marks}) "
                    f"AND security_id IN ({security_marks}) AND factor_date=? "
                    "AND available_from<=? ORDER BY factor_id,security_id",
                    [*factor_ids, *security_ids, self.as_of_date, self.as_of_date],
                )
            )
        )

    def benchmark_history(
        self, fields: Sequence[str], start: date, end: date
    ) -> tuple[dict[str, Any], ...]:
        return self.history(("000300.SH",), fields, start, end)

    def _validate_request(self, fields: Sequence[str], start: date, end: date) -> None:
        if not set(fields).issubset(self._declaration.required_fields):
            raise ResearchAccessError("STRATEGY_HISTORY_SLICE_VIOLATION: undeclared field")
        if start < self._earliest_date or end > self.as_of_date or start > end:
            raise ResearchAccessError("STRATEGY_HISTORY_SLICE_VIOLATION: history outside slice")
