from xqatexp.domain.enums import MarketRegime


def classify_market(
    close: float,
    ma20: float,
    ma60: float,
    slope60: float,
    drawdown60: float,
    volatility20: float,
) -> MarketRegime:
    if (
        (close < ma60 and ma20 < ma60 and slope60 <= 0.0)
        or drawdown60 <= -0.12
        or (volatility20 >= 0.40 and close < ma20)
    ):
        return MarketRegime.WEAK
    if close > ma20 > ma60 and slope60 > 0.0 and drawdown60 > -0.08 and volatility20 < 0.35:
        return MarketRegime.STRONG
    return MarketRegime.NEUTRAL
