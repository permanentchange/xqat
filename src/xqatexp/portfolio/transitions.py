from dataclasses import replace
from decimal import Decimal

from xqatexp.domain.contracts import TargetPortfolio, TargetTransitionRecord
from xqatexp.domain.enums import TargetTransition


def annotate_transitions(
    current: TargetPortfolio, previous: TargetPortfolio | None
) -> TargetPortfolio:
    prior = {} if previous is None else {item.security_id: item for item in previous.positions}
    positions = []
    records = []
    for item in current.positions:
        old = prior.get(item.security_id)
        if previous is None:
            transition = TargetTransition.INITIAL
            previous_weight = Decimal("0")
        elif old is None:
            transition = TargetTransition.NEW
            previous_weight = Decimal("0")
        elif abs(item.target_weight - old.target_weight) <= Decimal("1e-10"):
            transition = TargetTransition.RETAIN
            previous_weight = old.target_weight
        elif item.target_weight > old.target_weight:
            transition = TargetTransition.INCREASE
            previous_weight = old.target_weight
        else:
            transition = TargetTransition.DECREASE
            previous_weight = old.target_weight
        positions.append(replace(item, transition=transition))
        records.append(
            TargetTransitionRecord(
                item.security_id,
                previous_weight,
                item.target_weight,
                transition,
                item.explanation_codes,
            )
        )
    current_ids = {item.security_id for item in current.positions}
    if previous is not None:
        for item in previous.positions:
            if item.security_id not in current_ids:
                records.append(
                    TargetTransitionRecord(
                        item.security_id,
                        item.target_weight,
                        Decimal("0"),
                        TargetTransition.EXIT,
                        ("TARGET_EXIT",),
                    )
                )
    return replace(
        current,
        positions=tuple(positions),
        transition_records=tuple(sorted(records, key=lambda item: item.security_id)),
    )
