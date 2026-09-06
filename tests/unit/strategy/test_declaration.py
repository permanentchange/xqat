from xqatexp.strategy.declaration import strategy_declaration


def test_declaration_adds_custom_dependency_only_when_enabled() -> None:
    default = strategy_declaration({"custom_factor_name": None})
    custom = strategy_declaration({"custom_factor_name": "quality_score"})
    assert default.lookback_trade_days == 320
    assert len(default.required_system_factors) == 15
    assert default.required_custom_factors == ()
    assert custom.required_custom_factors == ("quality_score",)
