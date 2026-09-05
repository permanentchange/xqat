from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

FEN = Decimal("0.01")


def quantize_fen(value: Decimal) -> Decimal:
    return value.quantize(FEN, rounding=ROUND_HALF_UP)


def quantize_price(value: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        raise ValueError("price tick must be positive")
    ticks = (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return ticks * tick


def normalize_factor(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("factor value must be finite")
    return 0.0 if value == 0.0 else value
