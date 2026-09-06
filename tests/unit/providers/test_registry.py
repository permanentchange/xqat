from __future__ import annotations

import importlib

import pytest


def _registry():
    try:
        return importlib.import_module("xqatexp.providers.tushare.registry")
    except ModuleNotFoundError:
        pytest.fail("Tushare dataset registry is not implemented", pytrace=False)


def test_dataset_registry_freezes_all_required_interfaces() -> None:
    """Catches missing datasets or silent replacement by a different API."""
    registry = _registry()
    assert registry.dataset_ids() == (
        "dividend",
        "fina_indicator",
        "fund_adj_factor",
        "fund_basic",
        "fund_daily",
        "income",
        "index_daily",
        "stock_adj_factor",
        "stock_basic",
        "stock_daily",
        "stock_daily_basic",
        "stock_price_limit",
        "stock_st_status",
        "stock_suspend",
        "trade_calendar",
    )
    assert registry.get_dataset("stock_st_status").api_name == "stock_st"
    assert registry.get_dataset("fund_adj_factor").api_name == "fund_adj"
    assert registry.get_dataset("index_daily").fixed_params == {"ts_code": "000300.SH"}
    assert "st" not in {spec.api_name for spec in registry.all_datasets()}


def test_registry_has_exact_stock_daily_fields_and_key() -> None:
    """Catches field/unit mapping drift before a provider request is sent."""
    spec = _registry().get_dataset("stock_daily")
    assert spec.fields == (
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    )
    assert spec.business_key == ("ts_code", "trade_date")
    assert spec.volume_unit == "hand"
    assert spec.amount_unit == "thousand_cny"
