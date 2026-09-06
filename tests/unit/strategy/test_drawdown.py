from decimal import Decimal

from xqatexp.domain.enums import MarketRegime
from xqatexp.strategy.drawdown import DrawdownOverlay, OverlayLevel, rolling_drawdown


def test_rolling_60_drawdown_golden_points_59_to_62() -> None:
    nav = [Decimal("100"), Decimal("120"), *([Decimal("90")] * 60)]
    expected = {59: Decimal("-0.25"), 60: Decimal("-0.25"), 61: Decimal("-0.25"), 62: Decimal("0")}
    for point, value in expected.items():
        drawdown, observations = rolling_drawdown(nav[:point])
        assert drawdown == value
        assert observations == min(point, 60)


def test_defensive_recovery_requires_two_nonweak_weeks() -> None:
    overlay = DrawdownOverlay()
    assert overlay.update(Decimal("-0.12"), MarketRegime.NEUTRAL) is OverlayLevel.DEFENSIVE
    assert overlay.update(Decimal("-0.04"), MarketRegime.NEUTRAL) is OverlayLevel.DEFENSIVE
    assert overlay.update(Decimal("-0.03"), MarketRegime.STRONG) is OverlayLevel.NONE


def test_more_defensive_transition_wins_same_date() -> None:
    overlay = DrawdownOverlay()
    assert overlay.update(Decimal("-0.13"), MarketRegime.STRONG) is OverlayLevel.DEFENSIVE
