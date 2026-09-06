from datetime import date

from tests.unit.portfolio.test_validation import target
from xqatexp.daily.target import DailyTargetService


class _Strategy:
    def generate_target(self, research, custom, parameters):
        del research, custom, parameters
        return target()


def test_daily_target_calls_shared_strategy_and_adds_only_transitions() -> None:
    result = DailyTargetService().run(_Strategy(), object(), None, {}, previous=None)
    assert result.decision_date == date(2026, 9, 4)
    assert result.positions[0].transition.value == "INITIAL"
