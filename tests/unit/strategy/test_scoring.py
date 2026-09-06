from decimal import Decimal

from xqatexp.strategy.scoring import score_cross_section

WEIGHTS = {
    "momentum_60_ex5": Decimal("0.35"),
    "momentum_40": Decimal("0.20"),
    "trend_stability_60": Decimal("0.15"),
    "volume_price_confirm_20": Decimal("0.10"),
    "low_volatility_20": Decimal("0.10"),
    "profitability": Decimal("0.10"),
}


def _row(value: float, volatility: float) -> dict[str, float]:
    return {
        "momentum_60_ex5_v1": value,
        "momentum_40_v1": value,
        "trend_stability_60_v1": value,
        "volume_price_confirm_20_v1": value,
        "volatility_20_v1": volatility,
        "roe_annualized_v1": value,
    }


def test_six_factor_score_ranks_full_precision_then_security_id() -> None:
    scores = score_cross_section(
        {"600001.SH": _row(1.0, 2.0), "600000.SH": _row(2.0, 1.0)}, WEIGHTS
    )
    assert scores[0][0] == "600000.SH"
    assert scores[0][1] == 1.0
    assert scores[1][1] == 0.0


def test_custom_factor_direction_and_weight_change_score() -> None:
    records = {"600000.SH": _row(2.0, 1.0), "600001.SH": _row(1.0, 2.0)}
    custom = {"600000.SH": 0.0, "600001.SH": 1.0}
    result = score_cross_section(
        records,
        WEIGHTS,
        custom_values=custom,
        custom_weight=Decimal("0.20"),
        custom_direction="HIGHER_BETTER",
    )
    assert result[0][1] == 0.8
    assert result[1][1] == 0.2
