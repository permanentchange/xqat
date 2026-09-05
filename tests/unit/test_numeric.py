from __future__ import annotations

import importlib
import math
from decimal import Decimal

import pytest


def _numeric_module():
    try:
        return importlib.import_module("xqatexp.domain.numeric")
    except ModuleNotFoundError:
        pytest.fail("xqatexp.domain.numeric is not implemented", pytrace=False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("1.005"), Decimal("1.01")),
        (Decimal("-1.005"), Decimal("-1.01")),
        (Decimal("0"), Decimal("0.00")),
    ],
)
def test_quantize_fen_uses_decimal_half_up(raw: Decimal, expected: Decimal) -> None:
    """Catches binary-float or bankers-rounding leakage into fees."""
    assert _numeric_module().quantize_fen(raw) == expected


def test_quantize_price_uses_security_tick() -> None:
    """Catches hard-coded two-decimal price rounding for ETF/stock ticks."""
    numeric = _numeric_module()
    assert numeric.quantize_price(Decimal("10.025"), Decimal("0.01")) == Decimal("10.03")
    assert numeric.quantize_price(Decimal("3.1415"), Decimal("0.001")) == Decimal("3.142")


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_normalize_factor_rejects_nonfinite_values(value: float) -> None:
    """Catches NaN/Infinity entering factor sorting and JSON output."""
    with pytest.raises(ValueError, match="finite"):
        _numeric_module().normalize_factor(value)


def test_normalize_factor_canonicalizes_negative_zero() -> None:
    """Catches platform-dependent negative-zero serialization."""
    result = _numeric_module().normalize_factor(-0.0)
    assert result == 0.0
    assert math.copysign(1.0, result) == 1.0
