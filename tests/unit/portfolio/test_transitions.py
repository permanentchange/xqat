from dataclasses import replace
from decimal import Decimal

from tests.unit.portfolio.test_validation import target
from xqatexp.domain.enums import TargetTransition
from xqatexp.portfolio.transitions import annotate_transitions


def test_initial_and_explicit_previous_target_transitions() -> None:
    initial = annotate_transitions(target(), None)
    assert initial.positions[0].transition is TargetTransition.INITIAL
    previous = replace(
        target(),
        positions=(replace(target().positions[0], target_weight=Decimal("0.03")),),
        cash_weight=Decimal("0.97"),
    )
    annotated = annotate_transitions(target(), previous)
    assert annotated.positions[0].transition is TargetTransition.INCREASE


def test_missing_current_position_creates_exit_record() -> None:
    current = replace(target(), positions=(), cash_weight=Decimal("1"))
    annotated = annotate_transitions(current, target())
    assert annotated.transition_records[0].transition is TargetTransition.EXIT
