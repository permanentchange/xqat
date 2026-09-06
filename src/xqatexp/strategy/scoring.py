from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from xqatexp.research.factors import average_rank_percentiles, winsorize_type7

_COMPONENTS = (
    ("momentum_60_ex5", "momentum_60_ex5_v1", False),
    ("momentum_40", "momentum_40_v1", False),
    ("trend_stability_60", "trend_stability_60_v1", False),
    ("volume_price_confirm_20", "volume_price_confirm_20_v1", False),
    ("low_volatility_20", "volatility_20_v1", True),
    ("profitability", "roe_annualized_v1", False),
)


def _percentiles(
    security_ids: list[str], records: Mapping[str, Mapping[str, float]], field: str
) -> dict[str, float]:
    values = [records[security_id][field] for security_id in security_ids]
    ranked = average_rank_percentiles(winsorize_type7(values))
    return {
        security_id: percentile
        for security_id, percentile in zip(security_ids, ranked, strict=True)
        if percentile is not None
    }


def score_cross_section(
    records: Mapping[str, Mapping[str, float]],
    weights: Mapping[str, Decimal],
    *,
    custom_values: Mapping[str, float] | None = None,
    custom_weight: Decimal = Decimal("0"),
    custom_direction: str = "HIGHER_BETTER",
) -> tuple[tuple[str, float], ...]:
    security_ids = sorted(records)
    if len(security_ids) < 2:
        return ()
    scores = {security_id: Decimal("0") for security_id in security_ids}
    scale = Decimal("1") - custom_weight
    for weight_name, field, reverse in _COMPONENTS:
        ranks = _percentiles(security_ids, records, field)
        for security_id in security_ids:
            value = Decimal(str(ranks[security_id]))
            directed = Decimal("1") - value if reverse else value
            scores[security_id] += scale * weights[weight_name] * directed
    if custom_values is not None and custom_weight > 0:
        custom_records = {
            security_id: {"custom": custom_values[security_id]} for security_id in security_ids
        }
        ranks = _percentiles(security_ids, custom_records, "custom")
        for security_id in security_ids:
            value = Decimal(str(ranks[security_id]))
            if custom_direction == "LOWER_BETTER":
                value = Decimal("1") - value
            scores[security_id] += custom_weight * value
    return tuple(
        sorted(
            ((security_id, float(score)) for security_id, score in scores.items()),
            key=lambda item: (-item[1], item[0]),
        )
    )
