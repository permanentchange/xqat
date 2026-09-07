from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from xqatexp.domain.contracts import SecuritySnapshot
from xqatexp.domain.enums import AssetType, MarketRegime
from xqatexp.strategy.declaration import ETF_FACTOR_IDS
from xqatexp.strategy.drawdown import OverlayLevel
from xqatexp.strategy.weekly_strategy import WeeklyMarketGuardRankStrategy


class _Slice:
    def __init__(self, parent, as_of_date):
        self.parent = parent
        self.as_of_date = as_of_date

    def universe(self):
        values = {
            "is_listed": True,
            "listing_trade_days": 500,
            "is_st": False,
            "is_suspended_full_day": False,
            "risk_flags": [],
        }
        return (
            SecuritySnapshot("600000.SH", AssetType.A_SHARE, values),
            SecuritySnapshot("600001.SH", AssetType.A_SHARE, values),
        )

    def history(self, security_ids, fields, start, end):
        return tuple(
            {
                "security_id": security_id,
                "trade_date": day,
                "research_close": Decimal("110") if security_id == "510300.SH" else Decimal("10"),
            }
            for security_id in security_ids
            for day in self.parent.days
            if start <= day <= end
        )

    def system_factors(self, factor_ids, security_ids):
        etf = {
            "etf_ma20_v1": 100.0,
            "etf_ma60_v1": 90.0,
            "etf_slope60_v1": 0.10,
            "etf_drawdown60_v1": 0.0,
            "etf_volatility20_v1": 0.20,
        }
        rows = []
        for security_id in security_ids:
            for factor_id in factor_ids:
                if factor_id in ETF_FACTOR_IDS:
                    value = etf[factor_id]
                elif factor_id == "profit_positive_ttm_v1":
                    value = 1.0
                elif factor_id == "consecutive_loss_2_v1":
                    value = 0.0
                elif factor_id in {"total_mv_pct_v1", "amount_20d_pct_v1"}:
                    value = 0.8
                elif factor_id == "volatility_20_v1":
                    value = (
                        1.0
                        if self.parent.tie_system
                        else (1.0 if security_id == "600000.SH" else 2.0)
                    )
                else:
                    value = (
                        1.0
                        if self.parent.tie_system
                        else (2.0 if security_id == "600000.SH" else 1.0)
                    )
                rows.append(
                    {
                        "factor_id": factor_id,
                        "security_id": security_id,
                        "factor_date": self.as_of_date,
                        "value": value,
                        "quality_flags": [],
                    }
                )
        return tuple(rows)

    def benchmark_history(self, fields, start, end):
        return ()


class _View:
    def __init__(self, *, tie_system=False):
        start = date(2025, 1, 1)
        self.days = tuple(start + timedelta(days=index) for index in range(313))
        self.earliest_date = self.days[0]
        self.decision_date = self.days[-1]
        self.tie_system = tie_system

    def trading_days(self, start, end):
        return tuple(day for day in self.days if start <= day <= end)

    def slice(self, as_of_date):
        assert self.earliest_date <= as_of_date <= self.decision_date
        return _Slice(self, as_of_date)

    def next_trading_day(self, after):
        return after + timedelta(days=1)


def _parameters():
    return {
        "csi300_etf_id": "510300.SH",
        "entry_rank": 20,
        "exit_rank": 40,
        "min_holding_weeks": 2,
        "max_holding_weeks": 8,
        "min_listing_trade_days": 252,
        "min_size_percentile": Decimal("0.20"),
        "min_amount_percentile": Decimal("0.20"),
        "strong_stock_count": 20,
        "neutral_stock_count": 10,
        "single_stock_max_weight": Decimal("0.05"),
        "caution_drawdown": Decimal("-0.08"),
        "defensive_drawdown": Decimal("-0.12"),
        "recovery_drawdown": Decimal("-0.05"),
        "recovery_weeks": 2,
        "custom_factor_name": None,
        "custom_factor_weight": Decimal("0"),
        "custom_factor_direction": "HIGHER_BETTER",
        "score_weights": {
            "momentum_60_ex5": Decimal("0.35"),
            "momentum_40": Decimal("0.20"),
            "trend_stability_60": Decimal("0.15"),
            "volume_price_confirm_20": Decimal("0.10"),
            "low_volatility_20": Decimal("0.10"),
            "profitability": Decimal("0.10"),
        },
    }


def test_shared_strategy_replays_and_returns_strong_diversified_target() -> None:
    view = _View()
    target = WeeklyMarketGuardRankStrategy(_parameters()).generate_target(view, None, _parameters())
    assert target.market_regime is MarketRegime.STRONG
    assert target.drawdown_window_trade_days == 60
    assert target.drawdown_observations == 60
    assert [position.security_id for position in target.positions] == [
        "600000.SH",
        "600001.SH",
        "510300.SH",
    ]
    assert target.positions[0].target_weight == Decimal("0.05")
    assert sum((position.target_weight for position in target.positions), target.cash_weight) == 1


class _Custom:
    decision_date = date(2025, 11, 9)

    def values_at(self, factor_date, factor_ids, security_ids):
        return {"quality_score": {"600000.SH": 0.0, "600001.SH": 1.0}}


def test_enabled_custom_factor_changes_real_strategy_ranking() -> None:
    view = _View(tie_system=True)
    parameters = _parameters()
    parameters["custom_factor_name"] = "quality_score"
    parameters["custom_factor_weight"] = Decimal("0.20")
    target = WeeklyMarketGuardRankStrategy(parameters).generate_target(view, _Custom(), parameters)
    assert target.positions[0].security_id == "600001.SH"


def test_strategy_rejects_missing_warmup_regime_and_custom_inputs() -> None:
    parameters = _parameters()
    short = _View()
    short.days = short.days[:312]
    short.decision_date = short.days[-1]
    with pytest.raises(ValueError, match="STRATEGY_WARMUP_INSUFFICIENT"):
        WeeklyMarketGuardRankStrategy(parameters).generate_target(short, None, parameters)

    view = _View()
    missing_regime = view.slice(view.decision_date)
    missing_regime.system_factors = lambda _factor_ids, _security_ids: ()
    with pytest.raises(ValueError, match="ETF regime inputs"):
        WeeklyMarketGuardRankStrategy(parameters)._base_selection(
            missing_regime, "510300.SH", {}, None, parameters
        )

    parameters["custom_factor_name"] = "quality_score"
    parameters["custom_factor_weight"] = Decimal("0.20")
    with pytest.raises(ValueError, match="custom factor is required"):
        WeeklyMarketGuardRankStrategy(parameters)._base_selection(
            view.slice(view.decision_date), "510300.SH", {}, None, parameters
        )


def test_strategy_overlay_drift_and_calendar_fallback_edges() -> None:
    strategy = WeeklyMarketGuardRankStrategy(_parameters())
    assert strategy._apply_overlay(
        OverlayLevel.DEFENSIVE, Decimal("0.05"), 10, Decimal("0.30")
    ) == (Decimal("0"), Decimal("0.10"), Decimal("0.90"))
    assert strategy._apply_overlay(OverlayLevel.CAUTION, Decimal("0.05"), 0, Decimal("0.30")) == (
        Decimal("0"),
        Decimal("0.20"),
        Decimal("0.80"),
    )

    view = _View().slice(_View().decision_date)
    view.history = lambda *_args: ()
    assert strategy._drift(
        view,
        {"CASH": Decimal("20"), "600000.SH": Decimal("80")},
        date(2026, 9, 4),
        date(2026, 9, 7),
    ) == {"CASH": Decimal("20"), "600000.SH": Decimal("80")}

    class WeekdayOnly:
        pass

    assert strategy._next_day(WeekdayOnly(), date(2026, 9, 4)) == date(2026, 9, 7)
