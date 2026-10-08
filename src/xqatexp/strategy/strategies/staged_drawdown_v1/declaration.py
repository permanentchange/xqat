from __future__ import annotations

from collections.abc import Mapping

from xqatexp.domain.contracts import DataRequirement, StrategyDeclaration
from xqatexp.domain.enums import StateRequirement

from .parameters import StagedDrawdownParameters


def staged_drawdown_declaration(parameters: Mapping[str, object]) -> StrategyDeclaration:
    values = StagedDrawdownParameters.from_mapping(parameters)
    return StrategyDeclaration(
        strategy_id="staged_drawdown_v1",
        strategy_version="1.0.0",
        parameter_schema_version="1.0",
        decision_frequency="DAILY",
        lookback_trade_days=values.lookback_trade_days,
        required_fields=("research_close", "close_raw")
        + (("sell_lot_size",) if values.take_profit_mode == "tiered" else ()),
        required_system_factors=(),
        required_custom_factors=(),
        missing_policies={"price": "EXACT"},
        data_requirements=(
            DataRequirement(
                "trading_days",
                "TRADING_DAYS",
                (),
                values.lookback_trade_days,
                "MARKET",
                True,
                "EXACT",
                1.0,
                "DAILY",
                "STRATEGY_WARMUP_INSUFFICIENT",
            ),
            DataRequirement(
                "price_history",
                "MARKET_HISTORY",
                ("research_close", "close_raw"),
                values.lookback_trade_days,
                f"SECURITY:{values.security_id}",
                True,
                "EXACT",
                1.0,
                "DAILY",
                "DATA_COVERAGE_INSUFFICIENT",
            ),
        )
        + (
            (
                DataRequirement(
                    "sell_lot_size",
                    "SECURITY_RULES",
                    ("sell_lot_size",),
                    1,
                    f"SECURITY:{values.security_id}",
                    True,
                    "EXACT",
                    1.0,
                    "DAILY",
                    "DATA_REQUIRED_MISSING",
                ),
            )
            if values.take_profit_mode == "tiered"
            else ()
        ),
        state_requirement=StateRequirement.CONFIRMED_EXECUTION_STATE,
    )
