from __future__ import annotations

from collections.abc import Mapping

from xqatexp.domain.contracts import StrategyDeclaration

STOCK_FACTOR_IDS = (
    "total_mv_pct_v1",
    "amount_20d_pct_v1",
    "momentum_60_ex5_v1",
    "momentum_40_v1",
    "trend_stability_60_v1",
    "volume_price_confirm_20_v1",
    "volatility_20_v1",
    "roe_annualized_v1",
    "profit_positive_ttm_v1",
    "consecutive_loss_2_v1",
)
ETF_FACTOR_IDS = (
    "etf_ma20_v1",
    "etf_ma60_v1",
    "etf_slope60_v1",
    "etf_drawdown60_v1",
    "etf_volatility20_v1",
)


def strategy_declaration(parameters: Mapping[str, object]) -> StrategyDeclaration:
    custom_name = parameters.get("custom_factor_name")
    return StrategyDeclaration(
        strategy_id="weekly_market_guard_rank_v1",
        strategy_version="1.0.0",
        parameter_schema_version="1.0",
        decision_frequency="WEEKLY",
        lookback_trade_days=320,
        required_fields=("close_raw", "research_close"),
        required_system_factors=STOCK_FACTOR_IDS + ETF_FACTOR_IDS,
        required_custom_factors=(str(custom_name),) if custom_name else (),
        missing_policies={
            "price": "EXCLUDE_SECURITY",
            "financial": "EXCLUDE_SECURITY",
            "custom_factor": "EXACT",
        },
    )
