from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pyarrow.parquet as pq

from xqatexp.backtest.engine import BacktestEngine
from xqatexp.daily.account_snapshot import parse_account_snapshot
from xqatexp.daily.advice import DailyAdviceService
from xqatexp.daily.target import DailyTargetService
from xqatexp.domain.contracts import AccountSnapshot, ResolvedRunContext
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.portfolio.rebalance import LotRule
from xqatexp.portfolio.validation import validate_target
from xqatexp.reporting.publisher import ResultArtifactPublisher
from xqatexp.reporting.readers import load_target
from xqatexp.research.custom_factors import CsvCustomFactorView
from xqatexp.research.readiness import ReadinessChecker
from xqatexp.research.session import ResearchSession
from xqatexp.strategy.declaration import strategy_declaration
from xqatexp.strategy.weekly_strategy import WeeklyMarketGuardRankStrategy


class StrategyWorkflowService:
    def __init__(self) -> None:
        self._publisher = ResultArtifactPublisher()

    def backtest(self, context: ResolvedRunContext, overwrite: OverwritePolicy) -> Path:
        if context.start_date is None or context.end_date is None:
            raise ValueError("CONFIG_VALUE_INVALID: backtest dates required")
        declaration = strategy_declaration(context.parameters)
        custom = self._custom(context, context.end_date)
        strategy = WeeklyMarketGuardRankStrategy(context.parameters)
        with ResearchSession(context.research_artifact_path, declaration) as session:
            readiness = ReadinessChecker().check(
                declaration, session.view(context.end_date), custom
            )
            if not readiness.is_ready:
                raise ValueError(f"{readiness.issues[0]}: backtest input is not ready")
            result = BacktestEngine().run(
                data=session,
                strategy=strategy,
                parameters=context.parameters,
                custom=custom,
                start_date=context.start_date,
                end_date=context.end_date,
                initial_cash=cast(Decimal, context.execution_assumptions["initial_cash"]),
                execution_assumptions=context.execution_assumptions,
            )
        return self._publisher.publish_backtest(context, result, overwrite).path

    def daily_target(self, context: ResolvedRunContext, overwrite: OverwritePolicy) -> Path:
        if context.decision_date is None:
            raise ValueError("CONFIG_VALUE_INVALID: decision_date required")
        declaration = strategy_declaration(context.parameters)
        custom = self._custom(context, context.decision_date)
        strategy = WeeklyMarketGuardRankStrategy(context.parameters)
        previous = (
            load_target(context.previous_target_path)
            if context.previous_target_path is not None
            else None
        )
        with ResearchSession(context.research_artifact_path, declaration) as session:
            view = session.view(context.decision_date)
            readiness = ReadinessChecker().check(declaration, view, custom)
            if not readiness.is_ready:
                raise ValueError(f"{readiness.issues[0]}: daily input is not ready")
            target = DailyTargetService().run(
                strategy, view, custom, context.parameters, previous=previous
            )
            validate_target(
                target,
                etf_id=str(context.parameters["csi300_etf_id"]),
                maximum_stock_weight=cast(Decimal, context.parameters["single_stock_max_weight"]),
                next_trade_day=session.next_trading_day(context.decision_date),
            )
        return self._publisher.publish_daily_target(
            context, target, (), readiness.limitations, overwrite
        ).path

    def daily_advice(
        self,
        context: ResolvedRunContext,
        target_path: Path,
        account_path: Path | None,
        overwrite: OverwritePolicy,
        run_started_at: datetime,
    ) -> Path:
        target = load_target(target_path)
        account: AccountSnapshot | None = (
            parse_account_snapshot(account_path, run_started_at=run_started_at)
            if account_path is not None
            else None
        )
        declaration = strategy_declaration(context.parameters)
        ids = {item.security_id for item in target.positions}
        if account is not None:
            ids.update(item.security_id for item in account.positions)
        with ResearchSession(context.research_artifact_path, declaration) as session:
            rows = session.execution_rows(target.decision_date, sorted(ids))
        prices = {
            str(row["security_id"]): Decimal(str(row["close_raw"]))
            for row in rows
            if row["close_raw"] is not None
        }
        rules = {
            str(row["security_id"]): LotRule(int(row["buy_lot_size"]), int(row["sell_lot_size"]))
            for row in rows
        }
        advice = DailyAdviceService().run(
            target=target,
            account=account,
            reference_prices=prices,
            lot_rules=rules,
            run_started_at=run_started_at,
        )
        context = replace(
            context,
            decision_date=target.decision_date,
            account_snapshot_path=account_path,
            previous_target_path=target_path,
        )
        return self._publisher.publish_daily_advice(
            context, target, advice, overwrite, account=account
        ).path

    @staticmethod
    def _custom(context: ResolvedRunContext, decision_date: date) -> CsvCustomFactorView | None:
        required = strategy_declaration(context.parameters).required_custom_factors
        if not required:
            return None
        if len(context.custom_factor_inputs) != 1:
            raise ValueError(
                "FACTOR_COVERAGE_INSUFFICIENT: exactly one custom factor file required"
            )
        master = pq.read_table(
            context.research_artifact_path / "tables/security_master.parquet"
        ).to_pylist()
        return CsvCustomFactorView.load(
            context.custom_factor_inputs[0].path,
            decision_date=decision_date,
            known_security_ids={str(row["security_id"]) for row in master},
            required_factor_ids=set(required),
        )
