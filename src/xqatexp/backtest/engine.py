from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, cast

from xqatexp.backtest.account import SimulatedAccount
from xqatexp.backtest.corporate_actions import CorporateActionProcessor, DividendAction
from xqatexp.backtest.decision_executor import AllocationDecisionExecutor, UnfilledRecord
from xqatexp.backtest.fees import FeeModel
from xqatexp.domain.contracts import (
    AllocationDecision,
    CustomFactorView,
    ExecutionRecord,
    ResearchDataView,
    TargetPortfolio,
)
from xqatexp.domain.enums import AssetType, OrderSide
from xqatexp.performance.contribution import contribution
from xqatexp.portfolio.transitions import annotate_transitions
from xqatexp.strategy.decision import stable_decision_id
from xqatexp.strategy.schedule import DecisionSchedule, WeeklyLastTradingDayCloseSchedule


class EngineData(Protocol):
    def trading_days(self, start: date, end: date) -> Sequence[date]: ...

    def next_trading_day(self, after: date) -> date: ...

    def view(self, decision_date: date) -> ResearchDataView: ...

    def execution_rows(
        self, execution_date: date, security_ids: Sequence[str]
    ) -> Sequence[Mapping[str, Any]]: ...

    def corporate_actions(self, start: date, end: date) -> Sequence[Mapping[str, Any]]: ...


class EngineStrategy(Protocol):
    def generate_target(
        self,
        research: ResearchDataView,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
    ) -> TargetPortfolio: ...


@dataclass(frozen=True, slots=True)
class PortfolioDailyRecord:
    valuation_date: date
    nav: Decimal
    daily_return: Decimal | None
    benchmark_close: Decimal | None
    benchmark_nav: Decimal | None
    benchmark_daily_return: Decimal | None
    cash_opportunity_cost_vs_benchmark: Decimal | None
    running_peak: Decimal
    drawdown: Decimal
    cash_available: Decimal
    cash_receivable: Decimal
    stock_market_value: Decimal
    etf_market_value: Decimal
    stock_return_contribution: Decimal | None
    etf_return_contribution: Decimal | None
    cash_cost_contribution: Decimal | None
    gross_exposure: Decimal
    one_way_turnover: Decimal
    two_way_adjustment_turnover: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    targets: tuple[TargetPortfolio, ...]
    trades: tuple[ExecutionRecord, ...]
    unfilled: tuple[UnfilledRecord, ...]
    portfolio_daily: tuple[PortfolioDailyRecord, ...]
    limitations: tuple[str, ...] = ()
    decisions: tuple[AllocationDecision, ...] = ()


class BacktestEngine:
    def __init__(self, fees: FeeModel | None = None) -> None:
        self._fees = fees or FeeModel.default()
        self._decision_executor = AllocationDecisionExecutor(self._fees)

    def run(
        self,
        *,
        data: EngineData,
        strategy: EngineStrategy,
        parameters: Mapping[str, object],
        custom: CustomFactorView | None,
        start_date: date,
        end_date: date,
        initial_cash: Decimal,
        execution_assumptions: Mapping[str, object],
        schedule: DecisionSchedule | None = None,
    ) -> BacktestResult:
        if initial_cash <= 0 or start_date > end_date:
            raise ValueError("CONFIG_VALUE_INVALID: invalid backtest range or initial_cash")
        days = tuple(data.trading_days(start_date, end_date))
        if not days:
            raise ValueError("DATA_REQUIRED_MISSING: no trading days in backtest range")
        slippage = Decimal(str(execution_assumptions["slippage_bps"]))
        participation = Decimal(str(execution_assumptions["max_volume_participation"]))
        account = SimulatedAccount(initial_cash)
        action_processor = CorporateActionProcessor()
        actions = self._load_corporate_actions(data, start_date, end_date, execution_assumptions)
        pending: dict[date, AllocationDecision] = {}
        targets: list[TargetPortfolio] = []
        decisions: list[AllocationDecision] = []
        trades: list[ExecutionRecord] = []
        unfilled: list[UnfilledRecord] = []
        daily: list[PortfolioDailyRecord] = []
        previous_target: TargetPortfolio | None = None
        previous_nav: Decimal | None = None
        previous_stock = Decimal("0")
        previous_etf = Decimal("0")
        previous_cash = initial_cash
        running_peak = initial_cash
        benchmark_base: Decimal | None = None
        previous_benchmark: Decimal | None = None
        decision_schedule = schedule or WeeklyLastTradingDayCloseSchedule()

        for current_day in days:
            account.release_sellable(current_day)
            declared_dividends = Decimal("0")
            for action in actions:
                if action.ex_date == current_day and action.event_id in account.entitlements:
                    declared_dividends += action_processor.apply_ex_date(account, action)
            for action in actions:
                if action.pay_date == current_day and action.event_id in account.entitlements:
                    action_processor.apply_pay_date(account, action)
                if (
                    action.stock_list_date == current_day
                    and action.event_id in account.entitlements
                ):
                    action_processor.apply_stock_list_date(account, action)
            day_trades: list[tuple[ExecutionRecord, AssetType]] = []
            two_way = Decimal("0")
            turnover_base: Decimal | None = None
            due = pending.pop(current_day, None)
            if due is not None:
                execution_result = self._decision_executor.execute(
                    data=data,
                    account=account,
                    decision=due,
                    execution_date=current_day,
                    slippage=slippage,
                    participation=participation,
                )
                day_trades = list(execution_result.trades)
                two_way = execution_result.two_way_adjustment_turnover
                turnover_base = execution_result.turnover_base
                unfilled.extend(execution_result.unfilled)
                trades.extend(record for record, _ in day_trades)

            stock_value, etf_value = self._market_values(data, account, current_day)
            nav = account.cash_available + account.cash_receivable + stock_value + etf_value
            if nav <= 0:
                raise ValueError("BACKTEST_ACCOUNT_CONSERVATION_BROKEN: nonpositive NAV")
            turnover_notional = sum((record.gross_amount for record, _ in day_trades), Decimal("0"))
            one_way = turnover_notional / (turnover_base if turnover_base is not None else nav)
            benchmark_close = self._benchmark_close(data, current_day)
            if benchmark_close is not None and benchmark_base is None:
                benchmark_base = benchmark_close
            benchmark_nav = (
                benchmark_close / benchmark_base
                if benchmark_close is not None and benchmark_base is not None
                else None
            )
            benchmark_return = (
                benchmark_close / previous_benchmark - 1
                if benchmark_close is not None and previous_benchmark is not None
                else None
            )
            cash_opportunity_cost = (
                previous_cash / previous_nav * benchmark_return
                if previous_nav is not None and benchmark_return is not None
                else None
            )
            if previous_nav is None:
                daily_return = None
                stock_contribution = None
                etf_contribution = None
                cash_contribution = None
            else:
                daily_return = nav / previous_nav - 1
                flows = self._flows(day_trades)
                stock_result = contribution(
                    nav_begin=previous_nav,
                    nav_end=nav,
                    stock_begin=previous_stock,
                    stock_end=stock_value,
                    stock_buys=flows[(AssetType.A_SHARE, OrderSide.BUY)],
                    stock_sells=flows[(AssetType.A_SHARE, OrderSide.SELL)],
                    stock_dividends=declared_dividends,
                    etf_begin=previous_etf,
                    etf_end=etf_value,
                    etf_buys=flows[(AssetType.CSI300_ETF, OrderSide.BUY)],
                    etf_sells=flows[(AssetType.CSI300_ETF, OrderSide.SELL)],
                    cash_begin=previous_cash,
                    cash_end=account.cash_available + account.cash_receivable,
                )
                stock_contribution = stock_result.stock
                etf_contribution = stock_result.etf
                cash_contribution = stock_result.cash_cost
            running_peak = max(running_peak, nav)
            daily.append(
                PortfolioDailyRecord(
                    current_day,
                    nav,
                    daily_return,
                    benchmark_close,
                    benchmark_nav,
                    benchmark_return,
                    cash_opportunity_cost,
                    running_peak,
                    nav / running_peak - 1,
                    account.cash_available,
                    account.cash_receivable,
                    stock_value,
                    etf_value,
                    (stock_contribution if previous_nav is not None else None),
                    (etf_contribution if previous_nav is not None else None),
                    (cash_contribution if previous_nav is not None else None),
                    (stock_value + etf_value) / nav,
                    one_way,
                    two_way,
                )
            )
            account.record_valuation(current_day, nav)
            previous_nav = nav
            previous_stock = stock_value
            previous_etf = etf_value
            previous_cash = account.cash_available + account.cash_receivable
            previous_benchmark = benchmark_close

            for action in actions:
                if action.record_date == current_day:
                    action_processor.record_entitlement(account, action)

            if decision_schedule.is_decision_day(data, current_day):
                research = data.view(current_day)
                decision_generator = getattr(strategy, "generate_decision", None)
                if callable(decision_generator):
                    decision = cast(
                        AllocationDecision,
                        decision_generator(research, custom, parameters),
                    )
                    generated = decision.target
                else:
                    generated = strategy.generate_target(research, custom, parameters)
                    decision = AllocationDecision(
                        stable_decision_id(
                            generated.strategy_id,
                            generated.strategy_version,
                            generated.decision_date,
                            generated.effective_from,
                            "ALLOCATION",
                        ),
                        generated,
                    )
                annotated = annotate_transitions(generated, previous_target)
                decision = replace(decision, target=annotated)
                if annotated.effective_from <= current_day:
                    raise ValueError("PORTFOLIO_INVALID_TARGET: target is not forward effective")
                if annotated.effective_from in pending:
                    raise ValueError("PORTFOLIO_INVALID_TARGET: duplicate effective date")
                pending[annotated.effective_from] = decision
                targets.append(annotated)
                decisions.append(decision)
                previous_target = annotated
        limitations = (
            ("DIVIDEND_TAX_NOT_PERSONALIZED",)
            if actions
            and str(execution_assumptions.get("dividend_tax_model", "PROVIDER_AFTER_TAX"))
            == "PROVIDER_AFTER_TAX"
            else ()
        )
        return BacktestResult(
            tuple(targets),
            tuple(trades),
            tuple(unfilled),
            tuple(daily),
            limitations,
            tuple(decisions),
        )

    @staticmethod
    def _benchmark_close(data: EngineData, on_date: date) -> Decimal | None:
        loader = getattr(data, "benchmark_close", None)
        if not callable(loader):
            return None
        value = Decimal(str(loader(on_date)))
        if value <= 0:
            raise ValueError("BACKTEST_VALUATION_MISSING: benchmark close")
        return value

    @staticmethod
    def _load_corporate_actions(
        data: EngineData,
        start_date: date,
        end_date: date,
        assumptions: Mapping[str, object],
    ) -> tuple[DividendAction, ...]:
        loader = getattr(data, "corporate_actions", None)
        if not callable(loader):
            return ()
        model = str(assumptions.get("dividend_tax_model", "PROVIDER_AFTER_TAX"))
        if model not in {"PROVIDER_AFTER_TAX", "FLAT_RATE"}:
            raise ValueError("CONFIG_VALUE_INVALID: invalid dividend_tax_model")
        rate = Decimal(str(assumptions.get("dividend_tax_rate", "0")))
        if not Decimal("0") <= rate <= Decimal("1"):
            raise ValueError("CONFIG_VALUE_INVALID: dividend_tax_rate must be in [0,1]")
        output: list[DividendAction] = []
        for row in loader(start_date, end_date):
            record_date = row.get("record_date")
            ex_date = row.get("ex_date")
            if not isinstance(record_date, date) or not isinstance(ex_date, date):
                raise ValueError("BACKTEST_CORPORATE_ACTION_UNSUPPORTED: missing event dates")
            action_type = str(row["action_type"])
            cash_source = (
                row.get("cash_per_share_after_tax")
                if model == "PROVIDER_AFTER_TAX"
                else row.get("cash_per_share_before_tax")
            )
            if cash_source is None:
                cash = Decimal("0")
            else:
                cash = Decimal(str(cash_source))
                if model == "FLAT_RATE":
                    cash *= Decimal("1") - rate
            pay_date = row.get("pay_date")
            stock_list_date = row.get("stock_list_date")
            if cash and not isinstance(pay_date, date):
                raise ValueError("BACKTEST_CORPORATE_ACTION_UNSUPPORTED: missing pay_date")
            stock_ratio = Decimal(str(row.get("stock_ratio") or "0"))
            split_ratio = Decimal(str(row.get("split_ratio") or "0"))
            if stock_ratio and not isinstance(stock_list_date, date):
                raise ValueError("BACKTEST_CORPORATE_ACTION_UNSUPPORTED: missing stock_list_date")
            output.append(
                DividendAction(
                    str(row["event_id"]),
                    str(row["security_id"]),
                    record_date,
                    ex_date,
                    pay_date if isinstance(pay_date, date) else ex_date,
                    stock_list_date if isinstance(stock_list_date, date) else ex_date,
                    cash,
                    stock_ratio,
                    action_type,
                    split_ratio,
                )
            )
        return tuple(output)

    @staticmethod
    def _rows_by_security(
        data: EngineData, on_date: date, security_ids: Sequence[str]
    ) -> dict[str, Mapping[str, Any]]:
        rows = {str(row["security_id"]): row for row in data.execution_rows(on_date, security_ids)}
        missing = set(security_ids) - set(rows)
        if missing:
            raise ValueError(f"BACKTEST_VALUATION_MISSING: {min(missing)} on {on_date}")
        return rows

    def _market_values(
        self, data: EngineData, account: SimulatedAccount, on_date: date
    ) -> tuple[Decimal, Decimal]:
        active = sorted(key for key, quantity in account.positions.items() if quantity)
        rows = self._rows_by_security(data, on_date, active)
        stock = Decimal("0")
        etf = Decimal("0")
        for security_id in active:
            row = rows[security_id]
            if row.get("close_raw") is None and not bool(row.get("is_suspended_full_day")):
                raise ValueError(f"BACKTEST_VALUATION_MISSING: {security_id} on {on_date}")
            close = self._decimal(row, "close_raw", fallback="valuation_close")
            value = Decimal(account.positions[security_id]) * close
            if AssetType(str(row["asset_type"])) is AssetType.A_SHARE:
                stock += value
            else:
                etf += value
        return stock, etf

    @staticmethod
    def _flows(
        day_trades: Sequence[tuple[ExecutionRecord, AssetType]],
    ) -> dict[tuple[AssetType, OrderSide], Decimal]:
        result = {
            (asset, side): Decimal("0")
            for asset in (AssetType.A_SHARE, AssetType.CSI300_ETF)
            for side in (OrderSide.BUY, OrderSide.SELL)
        }
        for record, asset in day_trades:
            result[(asset, record.side)] += record.gross_amount
        return result

    @staticmethod
    def _decimal(row: Mapping[str, Any], field: str, *, fallback: str | None = None) -> Decimal:
        value = row.get(field)
        if value is None and fallback is not None:
            value = row.get(fallback)
        if value is None:
            raise ValueError(f"BACKTEST_VALUATION_MISSING: {field}")
        result = Decimal(str(value))
        if result <= 0:
            raise ValueError(f"BACKTEST_VALUATION_MISSING: nonpositive {field}")
        return result
