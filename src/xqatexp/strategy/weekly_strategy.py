from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

from xqatexp.domain.contracts import (
    CustomFactorView,
    Explanation,
    ResearchDataSlice,
    ResearchDataView,
    SecuritySnapshot,
    TargetPortfolio,
    TargetPosition,
)
from xqatexp.domain.enums import AssetType, MarketRegime
from xqatexp.strategy.declaration import ETF_FACTOR_IDS, STOCK_FACTOR_IDS, strategy_declaration
from xqatexp.strategy.drawdown import DrawdownOverlay, OverlayLevel, rolling_drawdown
from xqatexp.strategy.market_regime import classify_market
from xqatexp.strategy.scoring import score_cross_section


def allocate_budget(
    regime: MarketRegime, selected_count: int, maximum_weight: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    budgets = {
        MarketRegime.STRONG: (Decimal("0.75"), Decimal("0.15")),
        MarketRegime.NEUTRAL: (Decimal("0.40"), Decimal("0.30")),
        MarketRegime.WEAK: (Decimal("0"), Decimal("0.10")),
    }
    stock_budget, etf_weight = budgets[regime]
    per_stock = (
        min(stock_budget / selected_count, maximum_weight) if selected_count > 0 else Decimal("0")
    )
    cash = Decimal("1") - etf_weight - per_stock * selected_count
    return per_stock, etf_weight, cash


def select_holdings(
    ranked_security_ids: tuple[str, ...],
    previous_ages: Mapping[str, int],
    *,
    target_count: int,
    entry_rank: int,
    exit_rank: int,
    min_holding_weeks: int,
    max_holding_weeks: int,
) -> dict[str, int]:
    ranks = {security_id: rank for rank, security_id in enumerate(ranked_security_ids, start=1)}
    retained: dict[str, int] = {}
    for security_id, prior_age in previous_ages.items():
        rank = ranks.get(security_id)
        if rank is None:
            continue
        if (
            prior_age < min_holding_weeks
            or (prior_age < max_holding_weeks and rank <= exit_rank)
            or (prior_age >= max_holding_weeks and rank <= entry_rank)
        ):
            retained[security_id] = prior_age + 1
    retained = dict(sorted(retained.items(), key=lambda item: ranks[item[0]])[:target_count])
    for security_id in ranked_security_ids:
        if len(retained) >= target_count or ranks[security_id] > entry_rank:
            break
        retained.setdefault(security_id, 1)
    return dict(sorted(retained.items(), key=lambda item: ranks[item[0]]))


class WeeklyMarketGuardRankStrategy:
    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.declaration = strategy_declaration(parameters)

    def generate_target(
        self,
        research: ResearchDataView,
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
    ) -> TargetPortfolio:
        days = tuple(research.trading_days(research.earliest_date, research.decision_date))
        if len(days) < 313:
            raise ValueError("STRATEGY_WARMUP_INSUFFICIENT: at least 313 trading days required")
        formal_days = days[-252:]
        decisions = self._weekly_last_days(formal_days)
        etf_id = str(parameters["csi300_etf_id"])
        ages: dict[str, int] = {}
        asset_values: dict[str, Decimal] = {"CASH": Decimal("100")}
        nav_history: list[Decimal] = []
        previous_day: date | None = None
        overlay = DrawdownOverlay(
            caution=cast(Decimal, parameters["caution_drawdown"]),
            defensive=cast(Decimal, parameters["defensive_drawdown"]),
            recovery=cast(Decimal, parameters["recovery_drawdown"]),
            recovery_weeks=int(cast(int, parameters["recovery_weeks"])),
        )
        final: (
            tuple[MarketRegime, dict[str, int], dict[str, int], dict[str, float], Decimal, int]
            | None
        ) = None
        for current_day in formal_days:
            current_slice = research.slice(current_day)
            if previous_day is not None:
                asset_values = self._drift(current_slice, asset_values, previous_day, current_day)
            nav = sum(asset_values.values(), Decimal("0"))
            nav_history.append(nav)
            if current_day in decisions:
                regime, selected, ranks, scores = self._base_selection(
                    current_slice, etf_id, ages, custom, parameters
                )
                ages = selected
                per_stock, etf_weight, cash_weight = allocate_budget(
                    regime,
                    len(selected),
                    cast(Decimal, parameters["single_stock_max_weight"]),
                )
                asset_values = {
                    **{security_id: nav * per_stock for security_id in selected},
                    etf_id: nav * etf_weight,
                    "CASH": nav * cash_weight,
                }
                drawdown, observations = rolling_drawdown(nav_history)
                overlay.update(drawdown, regime)
                final = regime, selected, ranks, scores, drawdown, observations
            previous_day = current_day
        if final is None or formal_days[-1] not in decisions:
            raise ValueError("STRATEGY_PARAMETER_INVALID: decision date must be weekly close")
        regime, selected, ranks, scores, drawdown, observations = final
        per_stock, etf_weight, cash_weight = allocate_budget(
            regime, len(selected), cast(Decimal, parameters["single_stock_max_weight"])
        )
        per_stock, etf_weight, cash_weight = self._apply_overlay(
            overlay.level, per_stock, len(selected), etf_weight
        )
        positions = [
            TargetPosition(
                security_id,
                AssetType.A_SHARE,
                per_stock,
                ranks[security_id],
                scores[security_id] * 100.0,
                None,
                ("ENTRY_RANK" if selected[security_id] == 1 else "RETAIN_EXIT_RANK",),
                selected[security_id],
            )
            for security_id in selected
            if per_stock > 0
        ]
        if etf_weight > 0:
            positions.append(
                TargetPosition(
                    etf_id,
                    AssetType.CSI300_ETF,
                    etf_weight,
                    None,
                    None,
                    None,
                    (f"REGIME_{regime.value}",),
                    None,
                )
            )
        effective = self._next_day(research, research.decision_date)
        return TargetPortfolio(
            self.declaration.strategy_id,
            self.declaration.strategy_version,
            research.decision_date,
            effective,
            regime,
            drawdown,
            60,
            observations,
            overlay.level.value,
            tuple(positions),
            (),
            cash_weight,
            (
                Explanation(f"REGIME_{regime.value}", "ETF market risk regime"),
                Explanation(f"DRAWDOWN_{overlay.level.value}", "Rolling theoretical drawdown"),
            ),
        )

    @staticmethod
    def _weekly_last_days(days: Sequence[date]) -> frozenset[date]:
        latest: dict[tuple[int, int], date] = {}
        for day in days:
            iso = day.isocalendar()
            latest[(iso.year, iso.week)] = day
        return frozenset(latest.values())

    def _base_selection(
        self,
        view: ResearchDataSlice,
        etf_id: str,
        ages: Mapping[str, int],
        custom: CustomFactorView | None,
        parameters: Mapping[str, object],
    ) -> tuple[MarketRegime, dict[str, int], dict[str, int], dict[str, float]]:
        etf_factors = self._factor_map(
            cast(Sequence[Mapping[str, Any]], view.system_factors(ETF_FACTOR_IDS, (etf_id,)))
        )
        close_rows = cast(
            Sequence[Mapping[str, Any]],
            view.history((etf_id,), ("research_close",), view.as_of_date, view.as_of_date),
        )
        if not close_rows or any(etf_factors.get(name) is None for name in ETF_FACTOR_IDS):
            raise ValueError("DATA_REQUIRED_MISSING: ETF regime inputs")
        regime = classify_market(
            float(close_rows[0]["research_close"]),
            etf_factors["etf_ma20_v1"],
            etf_factors["etf_ma60_v1"],
            etf_factors["etf_slope60_v1"],
            etf_factors["etf_drawdown60_v1"],
            etf_factors["etf_volatility20_v1"],
        )
        snapshots = [item for item in view.universe() if item.asset_type is AssetType.A_SHARE]
        factor_rows = cast(
            Sequence[Mapping[str, Any]],
            view.system_factors(STOCK_FACTOR_IDS, tuple(item.security_id for item in snapshots)),
        )
        grouped: dict[str, dict[str, float]] = {}
        for row in factor_rows:
            if row["value"] is not None:
                grouped.setdefault(row["security_id"], {})[row["factor_id"]] = float(row["value"])
        eligible = {
            item.security_id: grouped[item.security_id]
            for item in snapshots
            if self._eligible(item, grouped.get(item.security_id), parameters)
        }
        custom_values = None
        custom_name = parameters.get("custom_factor_name")
        if custom_name:
            if custom is None:
                raise ValueError("FACTOR_COVERAGE_INSUFFICIENT: custom factor is required")
            loaded = custom.values_at(view.as_of_date, (str(custom_name),), tuple(eligible))
            custom_values = cast(dict[str, dict[str, float]], loaded)[str(custom_name)]
            eligible = {key: value for key, value in eligible.items() if key in custom_values}
        scores_tuple = score_cross_section(
            eligible,
            cast(Mapping[str, Decimal], parameters["score_weights"]),
            custom_values=custom_values,
            custom_weight=cast(Decimal, parameters["custom_factor_weight"]),
            custom_direction=str(parameters["custom_factor_direction"]),
        )
        ranked = tuple(item[0] for item in scores_tuple)
        ranks = {security_id: index for index, security_id in enumerate(ranked, 1)}
        scores = dict(scores_tuple)
        target_count = (
            0
            if regime is MarketRegime.WEAK
            else int(
                cast(
                    int,
                    parameters["strong_stock_count"]
                    if regime is MarketRegime.STRONG
                    else parameters["neutral_stock_count"],
                )
            )
        )
        selected = select_holdings(
            ranked,
            ages,
            target_count=target_count,
            entry_rank=int(cast(int, parameters["entry_rank"])),
            exit_rank=int(cast(int, parameters["exit_rank"])),
            min_holding_weeks=int(cast(int, parameters["min_holding_weeks"])),
            max_holding_weeks=int(cast(int, parameters["max_holding_weeks"])),
        )
        return regime, selected, ranks, scores

    @staticmethod
    def _factor_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
        return {
            str(row["factor_id"]): float(row["value"]) for row in rows if row["value"] is not None
        }

    @staticmethod
    def _eligible(
        snapshot: SecuritySnapshot,
        factors: Mapping[str, float] | None,
        parameters: Mapping[str, object],
    ) -> bool:
        values = snapshot.values
        forbidden = {
            "DELISTING_PERIOD",
            "TERMINATION_CONFIRMED",
            "DATA_CONFLICT",
            "ST_COVERAGE_MISSING",
        }
        return bool(
            factors is not None
            and set(STOCK_FACTOR_IDS).issubset(factors)
            and values.get("is_listed") is True
            and values.get("is_st") is False
            and int(cast(int, values.get("listing_trade_days", 0)))
            >= int(cast(int, parameters["min_listing_trade_days"]))
            and factors["total_mv_pct_v1"]
            >= float(cast(Decimal, parameters["min_size_percentile"]))
            and factors["amount_20d_pct_v1"]
            >= float(cast(Decimal, parameters["min_amount_percentile"]))
            and factors["profit_positive_ttm_v1"] == 1.0
            and factors["consecutive_loss_2_v1"] == 0.0
            and values.get("is_suspended_full_day") is False
            and not forbidden.intersection(cast(Sequence[str], values.get("risk_flags", ())))
        )

    @staticmethod
    def _drift(
        view: ResearchDataSlice,
        values: Mapping[str, Decimal],
        previous_day: date,
        current_day: date,
    ) -> dict[str, Decimal]:
        result = {"CASH": values.get("CASH", Decimal("0"))}
        for security_id, amount in values.items():
            if security_id == "CASH":
                continue
            history = cast(
                Sequence[Mapping[str, Any]],
                view.history((security_id,), ("research_close",), previous_day, current_day),
            )
            if len(history) != 2:
                result[security_id] = amount
            else:
                result[security_id] = (
                    amount
                    * Decimal(str(history[-1]["research_close"]))
                    / Decimal(str(history[0]["research_close"]))
                )
        return result

    @staticmethod
    def _apply_overlay(
        level: OverlayLevel,
        per_stock: Decimal,
        count: int,
        etf_weight: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        if level is OverlayLevel.DEFENSIVE:
            return Decimal("0"), Decimal("0.10"), Decimal("0.90")
        if level is OverlayLevel.CAUTION:
            stock_total = min(per_stock * count, Decimal("0.40"))
            per_stock = stock_total / count if count else Decimal("0")
            etf_weight = min(etf_weight, Decimal("0.20"))
        return per_stock, etf_weight, Decimal("1") - per_stock * count - etf_weight

    @staticmethod
    def _next_day(research: ResearchDataView, decision: date) -> date:
        method = getattr(research, "next_trading_day", None)
        if callable(method):
            return cast(date, method(decision))
        candidate = decision + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
