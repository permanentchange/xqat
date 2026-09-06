from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Sequence
from datetime import UTC, date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from xqatexp import __version__
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublisher, PublishedArtifact
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.backtest.engine import BacktestResult
from xqatexp.domain.contracts import (
    AccountSnapshot,
    ExecutionRecord,
    ResolvedRunContext,
    TargetPortfolio,
    TradeAdvice,
)
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.domain.issues import Issue
from xqatexp.performance.metrics import PerformanceAnalyzer
from xqatexp.reporting.markdown import (
    backtest_markdown,
    daily_advice_markdown,
    daily_target_markdown,
)
from xqatexp.reporting.structured import (
    advice_csv,
    advice_value,
    issue_value,
    issues_value,
    resolved_context_value,
    target_positions_csv,
    target_value,
)


class ResultArtifactPublisher:
    def __init__(self) -> None:
        self._schemas = SchemaRegistry()
        self._publisher = ArtifactPublisher()

    def publish_daily_target(
        self,
        context: ResolvedRunContext,
        target: TargetPortfolio,
        issues: Sequence[Issue],
        limitations: Sequence[str],
        overwrite: OverwritePolicy,
    ) -> PublishedArtifact:
        config = resolved_context_value(context)
        target_data = target_value(target)
        issue_data = issues_value(issues)
        self._schemas.validate_json("resolved_config", config)
        self._schemas.validate_json("target_portfolio", target_data)
        self._schemas.validate_json("issues", issue_data)

        def build(staging: Path) -> None:
            payloads = {
                "resolved_config.json": canonical_json_bytes(config),
                "target_portfolio.json": canonical_json_bytes(target_data),
                "target_positions.csv": target_positions_csv(target),
                "issues.json": canonical_json_bytes(issue_data),
                "report.md": daily_target_markdown(target_data).encode("utf-8"),
            }
            for name, payload in payloads.items():
                (staging / name).write_bytes(payload)
            manifest = self._manifest(
                context,
                "DAILY_TARGET_RESULT",
                payloads,
                target.decision_date,
                target.decision_date,
                issues,
                limitations,
            )
            (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

        return self._publisher.publish(build, context.output_path, overwrite)

    def publish_daily_advice(
        self,
        context: ResolvedRunContext,
        target: TargetPortfolio,
        advice: TradeAdvice,
        overwrite: OverwritePolicy,
        *,
        account: AccountSnapshot | None = None,
    ) -> PublishedArtifact:
        config = resolved_context_value(context)
        target_data = target_value(target)
        advice_data = advice_value(advice, account)
        issue_data = issues_value(advice.issues)
        self._schemas.validate_json("resolved_config", config)
        self._schemas.validate_json("target_portfolio", target_data)
        self._schemas.validate_json("trade_advice", advice_data)
        self._schemas.validate_json("issues", issue_data)

        def build(staging: Path) -> None:
            payloads = {
                "resolved_config.json": canonical_json_bytes(config),
                "target_portfolio.json": canonical_json_bytes(target_data),
                "target_positions.csv": target_positions_csv(target),
                "trade_advice.json": canonical_json_bytes(advice_data),
                "trade_advice.csv": advice_csv(advice),
                "issues.json": canonical_json_bytes(issue_data),
                "report.md": daily_advice_markdown(target_data, advice_data).encode("utf-8"),
            }
            for name, payload in payloads.items():
                (staging / name).write_bytes(payload)
            manifest = self._manifest(
                context,
                "DAILY_ADVICE_RESULT",
                payloads,
                target.decision_date,
                target.decision_date,
                advice.issues,
                advice.limitations,
            )
            (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

        return self._publisher.publish(build, context.output_path, overwrite)

    def publish_backtest(
        self,
        context: ResolvedRunContext,
        result: BacktestResult,
        overwrite: OverwritePolicy,
    ) -> PublishedArtifact:
        if not result.portfolio_daily:
            raise ValueError("PERFORMANCE_INSUFFICIENT_SAMPLE: no valuations")
        config = resolved_context_value(context)
        tables = self._backtest_tables(result)
        metrics = self._metrics(result)
        issue_data = issues_value(())
        self._schemas.validate_json("resolved_config", config)
        self._schemas.validate_json("metrics", metrics)
        self._schemas.validate_json("issues", issue_data)

        def build(staging: Path) -> None:
            payloads: dict[str, bytes] = {
                "resolved_config.json": canonical_json_bytes(config),
                "metrics.json": canonical_json_bytes(metrics),
                "period_metrics.csv": self._period_metrics_csv(metrics, result),
                "issues.json": canonical_json_bytes(issue_data),
                "report.md": backtest_markdown(metrics, len(result.trades)).encode("utf-8"),
            }
            for name, table in tables.items():
                path = staging / name
                pq.write_table(table, path, compression="zstd")
                payloads[name] = path.read_bytes()
            for name, payload in payloads.items():
                if not (staging / name).exists():
                    (staging / name).write_bytes(payload)
            first = result.portfolio_daily[0].valuation_date
            last = result.portfolio_daily[-1].valuation_date
            metric_limitations = metrics["limitations"]
            assert isinstance(metric_limitations, list)
            manifest = self._manifest(
                context, "BACKTEST_RESULT", payloads, first, last, (), metric_limitations
            )
            (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

        return self._publisher.publish(build, context.output_path, overwrite)

    def _backtest_tables(self, result: BacktestResult) -> dict[str, pa.Table]:
        target_rows = []
        for target in result.targets:
            for item in target.positions:
                target_rows.append(
                    {
                        "decision_date": target.decision_date,
                        "effective_from": target.effective_from,
                        "strategy_id": target.strategy_id,
                        "strategy_version": target.strategy_version,
                        "market_regime": target.market_regime.value,
                        "theoretical_drawdown": self._q12(target.theoretical_drawdown),
                        "drawdown_window_trade_days": target.drawdown_window_trade_days,
                        "drawdown_observations": target.drawdown_observations,
                        "drawdown_overlay_level": target.drawdown_overlay_level,
                        "security_id": item.security_id,
                        "asset_type": item.asset_type.value,
                        "target_weight": self._q12(item.target_weight),
                        "rank": item.rank,
                        "score": item.score,
                        "holding_age_weeks": item.holding_age_weeks,
                        "transition": (
                            item.transition.value if item.transition is not None else None
                        ),
                        "explanation_codes": list(item.explanation_codes),
                    }
                )
            target_rows.append(
                {
                    "decision_date": target.decision_date,
                    "effective_from": target.effective_from,
                    "strategy_id": target.strategy_id,
                    "strategy_version": target.strategy_version,
                    "market_regime": target.market_regime.value,
                    "theoretical_drawdown": self._q12(target.theoretical_drawdown),
                    "drawdown_window_trade_days": target.drawdown_window_trade_days,
                    "drawdown_observations": target.drawdown_observations,
                    "drawdown_overlay_level": target.drawdown_overlay_level,
                    "security_id": "CASH",
                    "asset_type": "CASH",
                    "target_weight": self._q12(target.cash_weight),
                    "rank": None,
                    "score": None,
                    "holding_age_weeks": None,
                    "transition": None,
                    "explanation_codes": [],
                }
            )
        target_rows.sort(key=lambda row: (row["decision_date"], row["security_id"]))

        daily_rows = [
            {
                "valuation_date": item.valuation_date,
                "nav": self._q4(item.nav),
                "daily_return": self._optional_q12(item.daily_return),
                "benchmark_close": (None if item.benchmark_close is None else item.benchmark_close),
                "benchmark_nav": self._optional_q12(item.benchmark_nav),
                "benchmark_daily_return": self._optional_q12(item.benchmark_daily_return),
                "cash_opportunity_cost_vs_benchmark": self._optional_q12(
                    item.cash_opportunity_cost_vs_benchmark
                ),
                "running_peak": self._q4(item.running_peak),
                "drawdown": self._q12(item.drawdown),
                "cash_available": self._q4(item.cash_available),
                "cash_receivable": self._q4(item.cash_receivable),
                "stock_market_value": self._q4(item.stock_market_value),
                "etf_market_value": self._q4(item.etf_market_value),
                "stock_return_contribution": self._optional_q12(item.stock_return_contribution),
                "etf_return_contribution": self._optional_q12(item.etf_return_contribution),
                "cash_cost_contribution": self._optional_q12(item.cash_cost_contribution),
                "gross_exposure": self._q12(item.gross_exposure),
                "one_way_turnover": self._q12(item.one_way_turnover),
                "two_way_adjustment_turnover": self._q12(item.two_way_adjustment_turnover),
            }
            for item in result.portfolio_daily
        ]
        trades = []
        for record in result.trades:
            decision = self._decision_date(result, record.execution_date)
            instruction_id = self._stable_id(
                "instruction", decision, record.security_id, record.side.value
            )
            execution_id = self._stable_id(
                instruction_id, record.execution_date, record.filled_quantity
            )
            reference_price = record.reference_price or record.execution_price
            slippage_cost = Decimal(record.filled_quantity) * (
                record.execution_price - reference_price
                if record.side.value == "BUY"
                else reference_price - record.execution_price
            )
            trades.append(
                {
                    "execution_id": execution_id,
                    "instruction_id": instruction_id,
                    "decision_date": decision,
                    "execution_date": record.execution_date,
                    "security_id": record.security_id,
                    "side": record.side.value,
                    "priority": 0 if record.side.value == "SELL" else 1,
                    "requested_quantity": record.requested_quantity,
                    "filled_quantity": record.filled_quantity,
                    "execution_price": record.execution_price,
                    "reference_price": reference_price,
                    "gross_amount": self._q4(record.gross_amount),
                    "commission": self._q4(record.fees.commission),
                    "transfer_fee": self._q4(record.fees.transfer_fee),
                    "stamp_duty": self._q4(record.fees.stamp_duty),
                    "total_fees": self._q4(record.fees.total),
                    "slippage_cost": self._q4(slippage_cost),
                    "status": record.status.value,
                }
            )
        trades.sort(
            key=lambda row: (
                row["execution_date"],
                row["priority"],
                row["security_id"],
                row["execution_id"],
            )
        )
        unfilled = []
        for unfilled_item in result.unfilled:
            instruction_id = self._stable_id(
                "instruction",
                unfilled_item.decision_date,
                unfilled_item.security_id,
                unfilled_item.side.value,
            )
            unfilled.append(
                {
                    "instruction_id": instruction_id,
                    "decision_date": unfilled_item.decision_date,
                    "execution_date": unfilled_item.execution_date,
                    "security_id": unfilled_item.security_id,
                    "side": unfilled_item.side.value,
                    "requested_quantity": unfilled_item.requested_quantity,
                    "filled_quantity": unfilled_item.filled_quantity,
                    "unfilled_quantity": unfilled_item.unfilled_quantity,
                    "reason": unfilled_item.reason.value,
                    "evidence": "{}",
                }
            )
        unfilled.sort(key=lambda row: (row["instruction_id"], row["execution_date"], row["reason"]))
        values = {
            "target_history.parquet": ("target_history", target_rows),
            "portfolio_daily.parquet": ("portfolio_daily", daily_rows),
            "trades.parquet": ("trades", trades),
            "unfilled.parquet": ("unfilled", unfilled),
        }
        output = {}
        for filename, (schema_id, rows) in values.items():
            table = pa.Table.from_pylist(rows, schema=self._schemas.arrow_schema(schema_id))
            self._schemas.validate_arrow(schema_id, table)
            output[filename] = table
        return output

    def _metrics(self, result: BacktestResult) -> dict[str, object]:
        points = tuple((item.valuation_date, item.nav) for item in result.portfolio_daily)
        performance = PerformanceAnalyzer().analyze(points, risk_free_rate=Decimal("0"))
        benchmark_values = tuple(
            item.benchmark_nav for item in result.portfolio_daily if item.benchmark_nav is not None
        )
        benchmark_complete = len(benchmark_values) == len(result.portfolio_daily)
        benchmark_return = benchmark_values[-1] - 1 if benchmark_complete else None
        limitations = set(performance.limitations) | set(result.limitations)
        if not benchmark_complete:
            limitations.add("BENCHMARK_UNAVAILABLE")
        rolling = {window: self._rolling_minimum(points, window) for window in (20, 60, 120)}
        for window, item in rolling.items():
            if item is None:
                limitations.add(f"ROLLING_{window}_INSUFFICIENT")
        return {
            "schema_version": "1.0",
            "formula_version": "1.0.0",
            "date_start": points[0][0].isoformat(),
            "date_end": points[-1][0].isoformat(),
            "valuation_points": performance.valuation_points,
            "return_intervals": performance.return_intervals,
            "initial_nav": points[0][1],
            "final_nav": points[-1][1],
            "cumulative_return": performance.cumulative_return,
            "benchmark_cumulative_return": benchmark_return,
            "excess_return": (
                None
                if benchmark_return is None
                else performance.cumulative_return - float(benchmark_return)
            ),
            "cash_opportunity_cost_vs_benchmark": sum(
                (
                    item.cash_opportunity_cost_vs_benchmark
                    for item in result.portfolio_daily
                    if item.cash_opportunity_cost_vs_benchmark is not None
                ),
                Decimal("0"),
            )
            if benchmark_complete
            else None,
            "annualized_return": performance.annualized_return,
            "annualized_volatility": performance.annualized_volatility,
            "max_drawdown": performance.max_drawdown,
            "max_drawdown_peak_date": performance.max_drawdown_peak_date.isoformat(),
            "max_drawdown_trough_date": performance.max_drawdown_trough_date.isoformat(),
            "max_drawdown_recovery_date": (
                performance.max_drawdown_recovery_date.isoformat()
                if performance.max_drawdown_recovery_date is not None
                else None
            ),
            "sharpe": performance.sharpe,
            "calmar": performance.calmar,
            "one_way_turnover": sum(
                (item.one_way_turnover for item in result.portfolio_daily), Decimal("0")
            ),
            "two_way_adjustment_turnover": sum(
                (item.two_way_adjustment_turnover for item in result.portfolio_daily),
                Decimal("0"),
            ),
            "total_commission": sum((item.fees.commission for item in result.trades), Decimal("0")),
            "total_transfer_fee": sum(
                (item.fees.transfer_fee for item in result.trades), Decimal("0")
            ),
            "total_stamp_duty": sum((item.fees.stamp_duty for item in result.trades), Decimal("0")),
            "total_slippage_cost": sum(
                (
                    Decimal(item.filled_quantity)
                    * (
                        item.execution_price - (item.reference_price or item.execution_price)
                        if item.side.value == "BUY"
                        else (item.reference_price or item.execution_price) - item.execution_price
                    )
                    for item in result.trades
                ),
                Decimal("0"),
            ),
            "stock_return_contribution": sum(
                (
                    item.stock_return_contribution
                    for item in result.portfolio_daily
                    if item.stock_return_contribution is not None
                ),
                Decimal("0"),
            ),
            "etf_return_contribution": sum(
                (
                    item.etf_return_contribution
                    for item in result.portfolio_daily
                    if item.etf_return_contribution is not None
                ),
                Decimal("0"),
            ),
            "cash_cost_contribution": sum(
                (
                    item.cash_cost_contribution
                    for item in result.portfolio_daily
                    if item.cash_cost_contribution is not None
                ),
                Decimal("0"),
            ),
            **{
                f"rolling_{window}_min_return": None if value is None else value[0]
                for window, value in rolling.items()
            },
            **{
                f"rolling_{window}_start_date": (None if value is None else value[1].isoformat())
                for window, value in rolling.items()
            },
            **{
                f"rolling_{window}_end_date": None if value is None else value[2].isoformat()
                for window, value in rolling.items()
            },
            "limitations": sorted(limitations),
        }

    @staticmethod
    def _rolling_minimum(
        points: Sequence[tuple[date, Decimal]], window: int
    ) -> tuple[Decimal, date, date] | None:
        if len(points) <= window:
            return None
        best: tuple[Decimal, date, date] | None = None
        for index in range(window, len(points)):
            value = points[index][1] / points[index - window][1] - 1
            candidate = (value, points[index - window][0], points[index][0])
            if best is None or value < best[0]:
                best = candidate
        return best

    @classmethod
    def _period_metrics_csv(cls, metrics: dict[str, object], result: BacktestResult) -> bytes:
        columns = (
            "period_type",
            "period_label",
            "start_date",
            "end_date",
            "valuation_points",
            "return_intervals",
            "cumulative_return",
            "annualized_return",
            "annualized_volatility",
            "max_drawdown",
            "sharpe",
            "calmar",
            "one_way_turnover",
            "total_fees",
            "total_slippage_cost",
            "limitations",
        )
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        limitations = metrics["limitations"]
        assert isinstance(limitations, list)
        rows: list[dict[str, object]] = [
            {
                "period_type": "FULL",
                "period_label": "ALL",
                "start_date": metrics["date_start"],
                "end_date": metrics["date_end"],
                "valuation_points": metrics["valuation_points"],
                "return_intervals": metrics["return_intervals"],
                "cumulative_return": metrics["cumulative_return"],
                "annualized_return": metrics["annualized_return"],
                "annualized_volatility": metrics["annualized_volatility"],
                "max_drawdown": metrics["max_drawdown"],
                "sharpe": metrics["sharpe"],
                "calmar": metrics["calmar"],
                "one_way_turnover": metrics["one_way_turnover"],
                "total_fees": Decimal(str(metrics["total_commission"]))
                + Decimal(str(metrics["total_transfer_fee"]))
                + Decimal(str(metrics["total_stamp_duty"])),
                "total_slippage_cost": metrics["total_slippage_cost"],
                "limitations": ";".join(value for value in limitations if isinstance(value, str)),
            }
        ]
        years = sorted({item.valuation_date.year for item in result.portfolio_daily})
        for year in years:
            records = tuple(
                item for item in result.portfolio_daily if item.valuation_date.year == year
            )
            points = tuple((item.valuation_date, item.nav) for item in records)
            performance = (
                PerformanceAnalyzer().analyze(points, risk_free_rate=Decimal("0"))
                if len(points) >= 2
                else None
            )
            trades = tuple(item for item in result.trades if item.execution_date.year == year)
            period_slippage = sum((cls._slippage_cost(item) for item in trades), Decimal("0"))
            rows.append(
                {
                    "period_type": "CALENDAR_YEAR",
                    "period_label": str(year),
                    "start_date": points[0][0],
                    "end_date": points[-1][0],
                    "valuation_points": len(points),
                    "return_intervals": max(0, len(points) - 1),
                    "cumulative_return": (
                        None if performance is None else performance.cumulative_return
                    ),
                    "annualized_return": (
                        None if performance is None else performance.annualized_return
                    ),
                    "annualized_volatility": (
                        None if performance is None else performance.annualized_volatility
                    ),
                    "max_drawdown": None if performance is None else performance.max_drawdown,
                    "sharpe": None if performance is None else performance.sharpe,
                    "calmar": None if performance is None else performance.calmar,
                    "one_way_turnover": sum(
                        (item.one_way_turnover for item in records), Decimal("0")
                    ),
                    "total_fees": sum((item.fees.total for item in trades), Decimal("0")),
                    "total_slippage_cost": period_slippage,
                    "limitations": (
                        "PERFORMANCE_INSUFFICIENT_SAMPLE"
                        if performance is None
                        else ";".join(performance.limitations)
                    ),
                }
            )
        rows.sort(
            key=lambda row: (
                str(row["start_date"]),
                str(row["period_type"]),
                str(row["period_label"]),
            )
        )
        writer.writerows(rows)
        return stream.getvalue().encode("utf-8")

    @staticmethod
    def _slippage_cost(record: ExecutionRecord) -> Decimal:
        reference = record.reference_price or record.execution_price
        direction = Decimal("1") if record.side.value == "BUY" else Decimal("-1")
        return Decimal(record.filled_quantity) * direction * (record.execution_price - reference)

    @staticmethod
    def _decision_date(result: BacktestResult, execution_date: date) -> date:
        candidates = [
            item.decision_date for item in result.targets if item.effective_from <= execution_date
        ]
        if not candidates:
            raise ValueError("BACKTEST_ACCOUNT_CONSERVATION_BROKEN: trade without target")
        return max(candidates)

    @staticmethod
    def _stable_id(*values: object) -> str:
        return hashlib.sha256(canonical_json_bytes([str(value) for value in values])).hexdigest()

    @staticmethod
    def _q4(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _q12(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)

    @classmethod
    def _optional_q12(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else cls._q12(value)

    @staticmethod
    def _manifest(
        context: ResolvedRunContext,
        artifact_type: str,
        payloads: dict[str, bytes],
        start: object,
        end: object,
        issues: Sequence[Issue],
        limitations: Sequence[str],
    ) -> dict[str, Any]:
        schema_by_name = {
            "resolved_config.json": "resolved_config",
            "target_portfolio.json": "target_portfolio",
            "target_positions.csv": "target_positions",
            "trade_advice.json": "trade_advice",
            "trade_advice.csv": "trade_advice",
            "target_history.parquet": "target_history",
            "portfolio_daily.parquet": "portfolio_daily",
            "trades.parquet": "trades",
            "unfilled.parquet": "unfilled",
            "metrics.json": "metrics",
            "period_metrics.csv": "period_metrics",
            "issues.json": "issues",
        }
        media = {
            ".json": "application/json",
            ".csv": "text/csv",
            ".md": "text/markdown",
            ".parquet": "application/vnd.apache.parquet",
        }
        files = []
        for name in sorted(payloads):
            payload = payloads[name]
            files.append(
                {
                    "path": name,
                    "media_type": media[Path(name).suffix],
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "row_count": None,
                    "schema_id": schema_by_name.get(name),
                    "schema_version": "1.0" if name in schema_by_name else None,
                }
            )
        return {
            "schema_version": "1.0",
            "artifact_type": artifact_type,
            "artifact_id": context.run_id,
            "created_at": context.generated_at.astimezone(UTC),
            "producer": {"name": "xqatexp", "version": __version__},
            "run": {"mode": context.mode, "strategy_id": context.strategy_id},
            "inputs": [
                {
                    "alias": "research",
                    "artifact_type": "RESEARCH_DATA",
                    "manifest_sha256": context.research_artifact_sha256,
                }
            ],
            "date_scope": {"start": start, "end": end},
            "files": files,
            "issues": [issue_value(item) for item in issues],
            "limitations": sorted(set(limitations)),
        }
