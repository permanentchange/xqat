from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.domain.contracts import SecuritySnapshot, StrategyDeclaration
from xqatexp.domain.enums import AssetType
from xqatexp.research.tables import TABLE_NAMES, ResearchCheckService


class ResearchAccessError(ValueError):
    pass


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


class ResearchSession:
    def __init__(self, path: Path, declaration: StrategyDeclaration) -> None:
        report = ResearchCheckService().check(path)
        if not report.valid:
            raise ResearchAccessError(f"DATA_INPUT_CORRUPT: {','.join(report.issue_codes)}")
        opened = ArtifactReader().open(path)
        self._connection = duckdb.connect(":memory:")
        for name in TABLE_NAMES:
            relation = self._connection.read_parquet(str(opened.path / f"tables/{name}.parquet"))
            relation.create_view(name)
        self._declaration = declaration
        self.closed = False

    def __enter__(self) -> ResearchSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self.closed:
            self._connection.close()
            self.closed = True

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
