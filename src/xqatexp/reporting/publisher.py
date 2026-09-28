from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from xqatexp import __version__
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublisher, PublishedArtifact
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.backtest.engine import BacktestResult
from xqatexp.domain.contracts import (
    AccountSnapshot,
    AllocationDecision,
    ResolvedRunContext,
    TargetPortfolio,
    TradeAdvice,
)
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.strategy.intents import TradeIntentDecision
from xqatexp.strategy.state import StrategyStateSnapshot
from xqatexp.strategy.state_io import strategy_state_value
from xqatexp.domain.issues import Issue
from xqatexp.reporting.assemblers import BacktestResultAssembler
from xqatexp.reporting.markdown import (
    backtest_markdown,
    daily_advice_markdown,
    daily_decision_markdown,
    daily_target_markdown,
)
from xqatexp.reporting.structured import (
    advice_csv,
    advice_value,
    issue_value,
    issues_value,
    resolved_context_value,
    strategy_diagnostics_value,
    target_positions_csv,
    trade_intents_value,
    target_value,
)


class ResultArtifactPublisher:
    def __init__(self) -> None:
        self._schemas = SchemaRegistry()
        self._publisher = ArtifactPublisher()
        self._backtest_assembler = BacktestResultAssembler(self._schemas)

    def publish_daily_target(
        self,
        context: ResolvedRunContext,
        target: TargetPortfolio,
        issues: Sequence[Issue],
        limitations: Sequence[str],
        overwrite: OverwritePolicy,
        *,
        decision: AllocationDecision | None = None,
    ) -> PublishedArtifact:
        config = resolved_context_value(context)
        target_data = target_value(target)
        diagnostics_data = strategy_diagnostics_value(() if decision is None else (decision,))
        issue_data = issues_value(issues)
        self._schemas.validate_json("resolved_config", config)
        self._schemas.validate_json("target_portfolio", target_data)
        self._schemas.validate_json("strategy_diagnostics", diagnostics_data)
        if state_data is not None:
            self._schemas.validate_json("strategy_state", state_data)
        self._schemas.validate_json("issues", issue_data)

        def build(staging: Path) -> None:
            payloads = {
                "resolved_config.json": canonical_json_bytes(config),
                "target_portfolio.json": canonical_json_bytes(target_data),
                "target_positions.csv": target_positions_csv(target),
                "strategy_diagnostics.json": canonical_json_bytes(diagnostics_data),
                **(
                    {}
                    if state_data is None
                    else {"strategy_state.json": canonical_json_bytes(state_data)}
                ),
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

    def publish_daily_decision(
        self,
        context: ResolvedRunContext,
        decision: TradeIntentDecision,
        state: StrategyStateSnapshot,
        issues: Sequence[Issue],
        limitations: Sequence[str],
        overwrite: OverwritePolicy,
    ) -> PublishedArtifact:
        config = resolved_context_value(context)
        intent_data = trade_intents_value(decision)
        state_data = strategy_state_value(state)
        diagnostics_data = strategy_diagnostics_value((decision,))
        issue_data = issues_value(issues)
        self._schemas.validate_json("resolved_config", config)
        self._schemas.validate_json("trade_intents", intent_data)
        self._schemas.validate_json("strategy_state", state_data)
        self._schemas.validate_json("strategy_diagnostics", diagnostics_data)
        self._schemas.validate_json("issues", issue_data)

        def build(staging: Path) -> None:
            payloads = {
                "resolved_config.json": canonical_json_bytes(config),
                "trade_intents.json": canonical_json_bytes(intent_data),
                "strategy_state.json": canonical_json_bytes(state_data),
                "strategy_diagnostics.json": canonical_json_bytes(diagnostics_data),
                "issues.json": canonical_json_bytes(issue_data),
                "report.md": daily_decision_markdown(intent_data).encode("utf-8"),
            }
            for name, payload in payloads.items():
                (staging / name).write_bytes(payload)
            manifest = self._manifest(
                context,
                "DAILY_DECISION_RESULT",
                payloads,
                decision.decision_date,
                decision.decision_date,
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
        assembly = self._backtest_assembler.assemble(result, context.analysis_periods)
        tables = assembly.tables
        metrics = assembly.metrics
        diagnostics_data = strategy_diagnostics_value(result.decisions)
        state_data = (
            None
            if result.strategy_state is None
            else strategy_state_value(result.strategy_state)
        )
        issue_data = issues_value(())
        self._schemas.validate_json("resolved_config", config)
        self._schemas.validate_json("metrics", metrics)
        self._schemas.validate_json("strategy_diagnostics", diagnostics_data)
        self._schemas.validate_json("issues", issue_data)

        def build(staging: Path) -> None:
            payloads: dict[str, bytes] = {
                "resolved_config.json": canonical_json_bytes(config),
                "metrics.json": canonical_json_bytes(metrics),
                "period_metrics.csv": assembly.period_metrics_csv,
                "strategy_diagnostics.json": canonical_json_bytes(diagnostics_data),
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
            "strategy_diagnostics.json": "strategy_diagnostics",
            "strategy_state.json": "strategy_state",
            "target_portfolio.json": "target_portfolio",
            "trade_intents.json": "trade_intents",
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
                    "schema_version": (
                        "2.0"
                        if name == "target_portfolio.json"
                        else "1.0"
                        if name in schema_by_name
                        else None
                    ),
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
