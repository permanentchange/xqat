from __future__ import annotations

from collections.abc import Mapping

from xqatexp.domain.contracts import DataRequirement, StrategyDeclaration

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
    etf_id = str(parameters.get("csi300_etf_id", "510300.SH"))
    requirements = [
        DataRequirement(
            "trading_days",
            "TRADING_DAYS",
            (),
            313,
            "MARKET",
            True,
            "EXACT",
            1.0,
            "DAILY",
            "STRATEGY_WARMUP_INSUFFICIENT",
        ),
        DataRequirement(
            "market_status",
            "MARKET_STATUS",
            ("is_st", "is_suspended_full_day"),
            252,
            "A_SHARE_ACTIVE",
            True,
            "EXCLUDE_SECURITY",
            0.98,
            "WEEKLY_LAST_TRADING_DAY",
            "DATA_COVERAGE_INSUFFICIENT",
        ),
        DataRequirement(
            "financial",
            "FINANCIAL",
            ("net_profit_parent_ttm", "roe_annualized", "consecutive_loss_quarters"),
            252,
            "A_SHARE_ACTIVE",
            True,
            "EXCLUDE_SECURITY",
            0.90,
            "WEEKLY_LAST_TRADING_DAY",
            "DATA_COVERAGE_INSUFFICIENT",
        ),
        DataRequirement(
            "system_factors",
            "SYSTEM_FACTORS",
            STOCK_FACTOR_IDS,
            252,
            "A_SHARE_ACTIVE",
            True,
            "EXCLUDE_SECURITY",
            0.98,
            "WEEKLY_LAST_TRADING_DAY",
            "FACTOR_COVERAGE_INSUFFICIENT",
        ),
        DataRequirement(
            "etf_factors",
            "SYSTEM_FACTORS",
            ETF_FACTOR_IDS,
            252,
            f"SECURITY:{etf_id}",
            True,
            "EXACT",
            1.0,
            "WEEKLY_LAST_TRADING_DAY",
            "FACTOR_COVERAGE_INSUFFICIENT",
        ),
    ]
    if custom_name:
        requirements.append(
            DataRequirement(
                "custom_factors",
                "CUSTOM_FACTORS",
                (str(custom_name),),
                252,
                "A_SHARE_ACTIVE",
                True,
                "EXACT",
                0.98,
                "WEEKLY_LAST_TRADING_DAY",
                "FACTOR_COVERAGE_INSUFFICIENT",
            )
        )
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
        data_requirements=tuple(requirements),
    )
