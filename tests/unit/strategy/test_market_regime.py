from xqatexp.domain.enums import MarketRegime
from xqatexp.strategy.market_regime import classify_market


def test_market_regime_exact_boundaries() -> None:
    assert classify_market(110, 105, 100, 0.1, -0.02, 0.2) is MarketRegime.STRONG
    assert classify_market(90, 95, 100, 0.0, -0.02, 0.2) is MarketRegime.WEAK
    assert classify_market(110, 105, 100, 0.1, -0.12, 0.2) is MarketRegime.WEAK
    assert classify_market(100, 100, 100, 0.1, -0.02, 0.2) is MarketRegime.NEUTRAL
    assert classify_market(99, 100, 98, 0.1, -0.02, 0.40) is MarketRegime.WEAK
