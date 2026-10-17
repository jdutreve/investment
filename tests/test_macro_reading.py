"""The macro reading joins the board to the invariants lit by it."""

from investment.mechanical import macro_reading as mr

GROUPS = {"CPIAUCSL": "Inflation", "GROWTH_COMPOSITE": "Growth", "BAA10Y": "Credit"}


def test_a_condition_belongs_to_the_themes_whose_series_it_reads() -> None:
    condition = [
        {"signal": "inflation", "feature": "level", "op": ">", "value": 2.5},
        {"signal": "regime", "feature": "type", "op": "==", "value": "stagflation"},
        {"signal": "credit_spread", "feature": "speed", "op": ">", "value": 0},
        {"signal": "inflation", "feature": "speed", "op": ">", "value": 0},
    ]
    assert mr.themes_of(condition, GROUPS) == ["Inflation", "Credit"]
    regime_only = [{"signal": "regime", "feature": "type", "op": "==", "value": "x"}]
    assert mr.themes_of(regime_only, GROUPS) == []


def test_a_condition_met_by_a_hair_is_not_lit() -> None:
    """2026-10-11: growth speed +0.10 against a usual spread that makes it flat."""
    rising = [{"signal": "growth", "feature": "speed", "op": ">", "value": 0.0}]
    spread = {"growth": {"speed": 1.0, "level": 10.0}}
    assert not mr.holds_clearly(rising, {"growth": {"speed": 0.10}}, spread, None)
    assert mr.holds_clearly(rising, {"growth": {"speed": 0.30}}, spread, None)
    below = [{"signal": "growth", "feature": "level", "op": "<", "value": 100.0}]
    assert mr.holds_clearly(below, {"growth": {"level": 97.0}}, spread, None)
    assert not mr.holds_clearly(below, {"growth": {"level": 99.0}}, spread, None)
    assert not mr.holds_clearly(below, {"growth": {"level": 101.0}}, spread, None)


def test_unknown_is_not_lit_and_the_regime_is_tested_plainly() -> None:
    rising = [{"signal": "growth", "feature": "speed", "op": ">", "value": 0.0}]
    assert not mr.holds_clearly(rising, {}, {"growth": {"speed": 1.0}}, None)
    assert not mr.holds_clearly(rising, {"growth": {"speed": 5.0}}, {}, None)
    regime = [{"signal": "regime", "feature": "type", "op": "==", "value": "stagflation"}]
    assert mr.holds_clearly(regime, {}, {}, "stagflation")
    assert not mr.holds_clearly(regime, {}, {}, None)
