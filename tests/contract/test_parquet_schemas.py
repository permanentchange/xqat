from __future__ import annotations

from datetime import date
from decimal import Decimal

import pyarrow as pa

from xqatexp.artifacts.schemas import SchemaRegistry


def test_portfolio_daily_schema_matches_contribution_contract() -> None:
    """Catches cash contribution naming/type drift between analysis and output."""
    schema = SchemaRegistry().arrow_schema("portfolio_daily")
    assert schema.names == [
        "valuation_date",
        "nav",
        "daily_return",
        "running_peak",
        "drawdown",
        "cash_available",
        "cash_receivable",
        "stock_market_value",
        "etf_market_value",
        "stock_return_contribution",
        "etf_return_contribution",
        "cash_cost_contribution",
        "gross_exposure",
        "one_way_turnover",
        "two_way_adjustment_turnover",
    ]
    assert schema.field("nav").type == pa.decimal128(20, 4)
    assert schema.field("cash_cost_contribution").type == pa.decimal128(18, 12)
    assert schema.field("daily_return").nullable


def test_arrow_validator_rejects_duplicate_business_keys() -> None:
    """Catches Parquet producers that publish duplicate valuation dates."""
    registry = SchemaRegistry()
    schema = registry.arrow_schema("portfolio_daily")
    rows = {
        field.name: [None, None] if field.nullable else [Decimal("0"), Decimal("0")]
        for field in schema
    }
    rows["valuation_date"] = [date(2026, 9, 4), date(2026, 9, 4)]
    rows["nav"] = [Decimal("1000"), Decimal("1000")]
    rows["running_peak"] = [Decimal("1000"), Decimal("1000")]
    rows["drawdown"] = [Decimal("0"), Decimal("0")]
    rows["cash_available"] = [Decimal("1000"), Decimal("1000")]
    rows["cash_receivable"] = [Decimal("0"), Decimal("0")]
    rows["stock_market_value"] = [Decimal("0"), Decimal("0")]
    rows["etf_market_value"] = [Decimal("0"), Decimal("0")]
    rows["gross_exposure"] = [Decimal("0"), Decimal("0")]
    rows["one_way_turnover"] = [Decimal("0"), Decimal("0")]
    rows["two_way_adjustment_turnover"] = [Decimal("0"), Decimal("0")]
    table = pa.Table.from_pydict(rows, schema=schema)

    try:
        registry.validate_arrow("portfolio_daily", table)
    except ValueError as error:
        assert "duplicate primary key" in str(error)
    else:
        raise AssertionError("duplicate valuation_date was accepted")


def test_csv_columns_are_frozen_for_all_human_exchange_files() -> None:
    """Catches column-order drift in strict CSV producers and consumers."""
    registry = SchemaRegistry()
    assert registry.csv_columns("custom_factor_input") == (
        "factor_name",
        "security_id",
        "factor_date",
        "factor_value",
    )
    assert registry.csv_columns("target_positions")[:5] == (
        "decision_date",
        "effective_from",
        "security_id",
        "asset_type",
        "target_weight",
    )
    assert registry.csv_columns("trade_advice")[-2:] == ("reason_codes", "limitations")
