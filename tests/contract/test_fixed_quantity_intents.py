from datetime import date

import pytest

from xqatexp.artifacts.schemas import SchemaRegistry, SchemaValidationError
from xqatexp.domain.enums import OrderSide
from xqatexp.reporting.structured import trade_intents_value
from xqatexp.strategy.intents import FixedQuantity, TradeIntent, TradeIntentDecision


def _value():
    return trade_intents_value(
        TradeIntentDecision(
            "decision",
            "staged_drawdown_v1",
            "1.0.0",
            date(2026, 9, 4),
            date(2026, 9, 7),
            (TradeIntent("intent", "600000.SH", OrderSide.SELL, FixedQuantity(300), ("TEST",)),),
        )
    )


def test_fixed_quantity_is_serialized_as_integer_and_validated() -> None:
    value = _value()
    assert value["intents"][0]["sizing"] == {"kind": "FIXED_QUANTITY", "value": 300}
    SchemaRegistry().validate_json("trade_intents", value)


@pytest.mark.parametrize("quantity", [0, -1, True, 0.5, None])
def test_schema_rejects_nonpositive_or_noninteger_fixed_quantity(quantity) -> None:
    value = _value()
    value["intents"][0]["sizing"]["value"] = quantity
    with pytest.raises(SchemaValidationError):
        SchemaRegistry().validate_json("trade_intents", value)
