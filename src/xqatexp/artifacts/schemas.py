from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pyarrow as pa


class SchemaValidationError(ValueError):
    """A structured value does not conform to its versioned schema."""


_SCHEMA_IDS = (
    "account_snapshot",
    "artifact_manifest",
    "custom_factor_input",
    "failure_diagnostic",
    "issues",
    "metrics",
    "period_metrics",
    "portfolio_daily",
    "raw_request",
    "raw_response",
    "research_tables",
    "resolved_config",
    "target_history",
    "target_portfolio",
    "target_positions",
    "trade_advice",
    "trades",
    "unfilled",
)

_JSON_FILES = {
    "account_snapshot": "account_snapshot.schema.json",
    "artifact_manifest": "artifact_manifest.schema.json",
    "failure_diagnostic": "failure_diagnostic.schema.json",
    "issues": "issues.schema.json",
    "metrics": "metrics.schema.json",
    "raw_request": "raw_request.schema.json",
    "resolved_config": "resolved_config.schema.json",
    "target_portfolio": "target_portfolio.schema.json",
    "trade_advice": "trade_advice.schema.json",
}

_CSV_COLUMNS = {
    "custom_factor_input": ("factor_name", "security_id", "factor_date", "factor_value"),
    "target_positions": (
        "decision_date",
        "effective_from",
        "security_id",
        "asset_type",
        "target_weight",
        "rank",
        "score",
        "holding_age_weeks",
        "transition",
        "explanation_codes",
    ),
    "period_metrics": (
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
    ),
    "trade_advice": (
        "decision_date",
        "effective_from",
        "security_id",
        "action",
        "current_quantity",
        "target_weight",
        "target_amount",
        "theoretical_target_quantity",
        "suggested_quantity",
        "max_confirmed_sell_quantity",
        "unresolved_quantity",
        "reference_price",
        "reference_price_date",
        "reason_codes",
        "limitations",
    ),
}

D18_12 = pa.decimal128(18, 12)
D18_6 = pa.decimal128(18, 6)
D20_4 = pa.decimal128(20, 4)
D24_4 = pa.decimal128(24, 4)
D24_10 = pa.decimal128(24, 10)
D24_12 = pa.decimal128(24, 12)

_ARROW_SCHEMAS = {
    "security_master": pa.schema(
        [
            pa.field("security_id", pa.string(), nullable=False),
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("exchange", pa.string(), nullable=False),
            pa.field("asset_type", pa.string(), nullable=False),
            pa.field("currency", pa.string(), nullable=False),
            pa.field("list_date", pa.date32(), nullable=False),
            pa.field("delist_date", pa.date32()),
            pa.field("buy_lot_size", pa.int64(), nullable=False),
            pa.field("sell_lot_size", pa.int64(), nullable=False),
            pa.field("price_tick", D18_6, nullable=False),
            pa.field("rule_effective_from", pa.date32(), nullable=False),
            pa.field("source_hash", pa.string(), nullable=False),
        ]
    ),
    "trade_calendar": pa.schema(
        [
            pa.field("exchange", pa.string(), nullable=False),
            pa.field("calendar_date", pa.date32(), nullable=False),
            pa.field("is_open", pa.bool_(), nullable=False),
            pa.field("previous_trade_date", pa.date32()),
            pa.field("next_trade_date", pa.date32()),
            pa.field("source_hash", pa.string(), nullable=False),
        ]
    ),
    "market_daily": pa.schema(
        [
            pa.field("security_id", pa.string(), nullable=False),
            pa.field("trade_date", pa.date32(), nullable=False),
            *[
                pa.field(name, D18_6)
                for name in ("open_raw", "high_raw", "low_raw", "close_raw", "pre_close_raw")
            ],
            pa.field("volume_shares", pa.int64()),
            pa.field("amount_cny", D24_4),
            pa.field("adj_factor", D24_12),
            *[
                pa.field(name, D24_10)
                for name in ("research_open", "research_high", "research_low", "research_close")
            ],
            pa.field("available_from", pa.date32(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ]
    ),
    "security_status_daily": pa.schema(
        [
            pa.field("security_id", pa.string(), nullable=False),
            pa.field("trade_date", pa.date32(), nullable=False),
            pa.field("is_listed", pa.bool_(), nullable=False),
            pa.field("listing_trade_days", pa.int32(), nullable=False),
            pa.field("is_st", pa.bool_()),
            pa.field("is_suspended_full_day", pa.bool_()),
            pa.field("up_limit", D18_6),
            pa.field("down_limit", D18_6),
            pa.field("is_limit_up_locked", pa.bool_()),
            pa.field("is_limit_down_locked", pa.bool_()),
            pa.field("risk_flags", pa.list_(pa.string()), nullable=False),
            pa.field("available_from", pa.date32(), nullable=False),
            pa.field("source_hash", pa.string(), nullable=False),
        ]
    ),
    "financial_snapshot": pa.schema(
        [
            pa.field("security_id", pa.string(), nullable=False),
            pa.field("report_period", pa.date32(), nullable=False),
            pa.field("announce_date", pa.date32(), nullable=False),
            pa.field("available_from", pa.date32(), nullable=False),
            pa.field("revision_seq", pa.int32(), nullable=False),
            pa.field("net_profit_parent_ytd", D24_4),
            pa.field("net_profit_parent_quarter", D24_4),
            pa.field("net_profit_parent_ttm", D24_4),
            pa.field("roe_annualized", D18_12),
            pa.field("consecutive_loss_quarters", pa.int32()),
            pa.field("source_hash", pa.string(), nullable=False),
        ]
    ),
    "system_factor_daily": pa.schema(
        [
            pa.field("factor_id", pa.string(), nullable=False),
            pa.field("factor_version", pa.string(), nullable=False),
            pa.field("security_id", pa.string(), nullable=False),
            pa.field("factor_date", pa.date32(), nullable=False),
            pa.field("value", pa.float64()),
            pa.field("available_from", pa.date32(), nullable=False),
            pa.field("input_start_date", pa.date32()),
            pa.field("input_end_date", pa.date32(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ]
    ),
    "corporate_action": pa.schema(
        [
            pa.field("event_id", pa.string(), nullable=False),
            pa.field("security_id", pa.string(), nullable=False),
            pa.field("action_type", pa.string(), nullable=False),
            pa.field("announce_date", pa.date32(), nullable=False),
            *[
                pa.field(name, pa.date32())
                for name in (
                    "implementation_announce_date",
                    "record_date",
                    "ex_date",
                    "pay_date",
                    "stock_list_date",
                )
            ],
            pa.field("cash_per_share_before_tax", D18_6),
            pa.field("cash_per_share_after_tax", D18_6),
            pa.field("stock_ratio", D18_12),
            pa.field("split_ratio", D18_12),
            pa.field("rights_ratio", D18_12),
            pa.field("rights_price", D18_6),
            pa.field("available_from", pa.date32(), nullable=False),
            pa.field("source_hash", pa.string(), nullable=False),
        ]
    ),
    "target_history": pa.schema(
        [
            pa.field("decision_date", pa.date32(), nullable=False),
            pa.field("effective_from", pa.date32(), nullable=False),
            pa.field("strategy_id", pa.string(), nullable=False),
            pa.field("strategy_version", pa.string(), nullable=False),
            pa.field("market_regime", pa.string(), nullable=False),
            pa.field("theoretical_drawdown", D18_12, nullable=False),
            pa.field("drawdown_window_trade_days", pa.int32(), nullable=False),
            pa.field("drawdown_observations", pa.int32(), nullable=False),
            pa.field("drawdown_overlay_level", pa.string(), nullable=False),
            pa.field("security_id", pa.string(), nullable=False),
            pa.field("asset_type", pa.string(), nullable=False),
            pa.field("target_weight", D18_12, nullable=False),
            pa.field("rank", pa.int32()),
            pa.field("score", pa.float64()),
            pa.field("holding_age_weeks", pa.int32()),
            pa.field("transition", pa.string()),
            pa.field("explanation_codes", pa.list_(pa.string()), nullable=False),
        ]
    ),
    "portfolio_daily": pa.schema(
        [
            pa.field("valuation_date", pa.date32(), nullable=False),
            pa.field("nav", D20_4, nullable=False),
            pa.field("daily_return", D18_12),
            pa.field("benchmark_close", D18_6),
            pa.field("benchmark_nav", D18_12),
            pa.field("benchmark_daily_return", D18_12),
            pa.field("cash_opportunity_cost_vs_benchmark", D18_12),
            pa.field("running_peak", D20_4, nullable=False),
            pa.field("drawdown", D18_12, nullable=False),
            pa.field("cash_available", D20_4, nullable=False),
            pa.field("cash_receivable", D20_4, nullable=False),
            pa.field("stock_market_value", D20_4, nullable=False),
            pa.field("etf_market_value", D20_4, nullable=False),
            pa.field("stock_return_contribution", D18_12),
            pa.field("etf_return_contribution", D18_12),
            pa.field("cash_cost_contribution", D18_12),
            pa.field("gross_exposure", D18_12, nullable=False),
            pa.field("one_way_turnover", D18_12, nullable=False),
            pa.field("two_way_adjustment_turnover", D18_12, nullable=False),
        ]
    ),
    "trades": pa.schema(
        [
            pa.field("execution_id", pa.string(), nullable=False),
            pa.field("instruction_id", pa.string(), nullable=False),
            pa.field("decision_date", pa.date32(), nullable=False),
            pa.field("execution_date", pa.date32(), nullable=False),
            pa.field("security_id", pa.string(), nullable=False),
            pa.field("side", pa.string(), nullable=False),
            pa.field("priority", pa.int32(), nullable=False),
            pa.field("requested_quantity", pa.int64(), nullable=False),
            pa.field("filled_quantity", pa.int64(), nullable=False),
            pa.field("execution_price", D18_6, nullable=False),
            pa.field("reference_price", D18_6, nullable=False),
            pa.field("gross_amount", D20_4, nullable=False),
            pa.field("commission", D20_4, nullable=False),
            pa.field("transfer_fee", D20_4, nullable=False),
            pa.field("stamp_duty", D20_4, nullable=False),
            pa.field("total_fees", D20_4, nullable=False),
            pa.field("slippage_cost", D20_4, nullable=False),
            pa.field("status", pa.string(), nullable=False),
        ]
    ),
    "unfilled": pa.schema(
        [
            pa.field("instruction_id", pa.string(), nullable=False),
            pa.field("decision_date", pa.date32(), nullable=False),
            pa.field("execution_date", pa.date32(), nullable=False),
            pa.field("security_id", pa.string(), nullable=False),
            pa.field("side", pa.string(), nullable=False),
            pa.field("requested_quantity", pa.int64(), nullable=False),
            pa.field("filled_quantity", pa.int64(), nullable=False),
            pa.field("unfilled_quantity", pa.int64(), nullable=False),
            pa.field("reason", pa.string(), nullable=False),
            pa.field("evidence", pa.string(), nullable=False),
        ]
    ),
}

_PRIMARY_KEYS = {
    "security_master": ("security_id",),
    "trade_calendar": ("exchange", "calendar_date"),
    "market_daily": ("security_id", "trade_date"),
    "security_status_daily": ("security_id", "trade_date"),
    "financial_snapshot": ("security_id", "report_period", "announce_date", "revision_seq"),
    "system_factor_daily": ("factor_id", "security_id", "factor_date", "factor_version"),
    "corporate_action": ("event_id",),
    "target_history": ("decision_date", "security_id"),
    "portfolio_daily": ("valuation_date",),
    "trades": ("execution_id",),
    "unfilled": ("instruction_id", "execution_date", "reason"),
}

_SORT_KEYS = {
    "trades": ("execution_date", "priority", "security_id", "execution_id"),
}


class SchemaRegistry:
    def __init__(self, schema_root: Path | None = None) -> None:
        if schema_root is not None:
            self._schema_root = schema_root
        else:
            source_root = Path(__file__).resolve().parents[3] / "schemas"
            installed_root = Path(__file__).resolve().parents[2] / "schemas"
            self._schema_root = source_root if source_root.is_dir() else installed_root

    @property
    def schema_ids(self) -> tuple[str, ...]:
        return _SCHEMA_IDS

    @property
    def json_schema_ids(self) -> tuple[str, ...]:
        return tuple(sorted(_JSON_FILES))

    def load_json_schema(self, schema_id: str) -> dict[str, Any]:
        try:
            filename = _JSON_FILES[schema_id]
        except KeyError as error:
            raise SchemaValidationError(
                f"ARTIFACT_SCHEMA_INCOMPATIBLE: unknown JSON schema {schema_id}"
            ) from error
        loaded = json.loads((self._schema_root / filename).read_text("utf-8"))
        if not isinstance(loaded, dict):
            raise SchemaValidationError(
                f"ARTIFACT_SCHEMA_INCOMPATIBLE: {schema_id} schema root is not an object"
            )
        return loaded

    def validate_json(self, schema_id: str, value: Any) -> None:
        if schema_id not in _JSON_FILES:
            raise SchemaValidationError(
                f"ARTIFACT_SCHEMA_INCOMPATIBLE: unknown JSON schema {schema_id}"
            )
        version = value.get("schema_version") if isinstance(value, dict) else None
        if not isinstance(version, str) or version.split(".", 1)[0] != "1":
            raise SchemaValidationError(
                f"ARTIFACT_SCHEMA_INCOMPATIBLE: unsupported {schema_id} version {version!r}"
            )
        schema = self.load_json_schema(schema_id)
        validator = jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        )
        errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
        if errors:
            path = ".".join(str(part) for part in errors[0].path) or "$"
            code = (
                "CONFIG_SCHEMA_INVALID"
                if schema_id == "account_snapshot"
                else "ARTIFACT_SCHEMA_INCOMPATIBLE"
            )
            raise SchemaValidationError(f"{code}: {path}: {errors[0].message}")

    def arrow_schema(self, schema_id: str) -> pa.Schema:
        try:
            return _ARROW_SCHEMAS[schema_id]
        except KeyError as error:
            raise SchemaValidationError(
                f"ARTIFACT_SCHEMA_INCOMPATIBLE: no Arrow schema {schema_id}"
            ) from error

    def csv_columns(self, schema_id: str) -> tuple[str, ...]:
        try:
            return _CSV_COLUMNS[schema_id]
        except KeyError as error:
            raise SchemaValidationError(
                f"ARTIFACT_SCHEMA_INCOMPATIBLE: no CSV schema {schema_id}"
            ) from error

    def validate_arrow(self, schema_id: str, table: pa.Table) -> None:
        expected = self.arrow_schema(schema_id)
        if table.schema != expected:
            raise SchemaValidationError(f"ARTIFACT_SCHEMA_INCOMPATIBLE: {schema_id} Arrow schema")
        key_names = _PRIMARY_KEYS[schema_id]
        keys = list(zip(*(table.column(name).to_pylist() for name in key_names), strict=True))
        if len(keys) != len(set(keys)):
            raise SchemaValidationError(f"DATA_CONFLICT: {schema_id} duplicate primary key")
        sort_names = _SORT_KEYS.get(schema_id, key_names)
        sort_keys = list(zip(*(table.column(name).to_pylist() for name in sort_names), strict=True))
        if sort_keys != sorted(sort_keys):
            raise SchemaValidationError(f"DATA_CONFLICT: {schema_id} rows are not sorted")
