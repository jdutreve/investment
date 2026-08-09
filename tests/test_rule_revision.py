"""Tests for `mechanical/rule_revision.py` — measuring a proposed rule change.

The M8b runs produced 22 innovations across two passes, most of them rule
revisions carrying no target allocation: no candidate portfolio, no NAV, no
FAVORS, and probation closes them as unmeasurable. This is the path that gives a
revision naming a KNOWN knob an answer in one pass instead of none ever.
"""

from typing import Any

import pytest

from investment.mechanical import market_signal, rule_revision
from investment.mechanical.replay import NavMetrics


def _measurement(
    *,
    sortino: tuple[float, float],
    drawdown: tuple[float, float],
    cagr: tuple[float, float] = (0.10, 0.10),
    calmar: tuple[float, float] = (1.0, 1.0),
) -> rule_revision.RevisionMeasurement:
    """A measurement with every indicator explicit, because the verdict is now
    PARETO over all four — a helper that pinned two of them and left the others
    equal would only ever exercise a quarter of the rule."""

    def metrics(c: float, s: float, k: float, d: float) -> NavMetrics:
        return NavMetrics(cagr=c, sortino=s, calmar=k, max_drawdown=d)

    return rule_revision.RevisionMeasurement(
        overrides={"confirm_decisions": 4},
        baseline=metrics(cagr[0], sortino[0], calmar[0], drawdown[0]),
        variant=metrics(cagr[1], sortino[1], calmar[1], drawdown[1]),
        baseline_turnover=42.0,
        variant_turnover=50.0,
    )


def test_only_the_registry_knobs_are_extracted() -> None:
    spec: dict[str, Any] = {
        "proposed_rule": "prose the model wrote, deliberately not parsed",
        "parameters": {"trend_sleeves": ["SPY", "GLD", "IWN"], "invent_a_new_signal": "HY_OAS"},
    }
    assert rule_revision.extract_overrides(spec) == {"trend_sleeves": ["SPY", "GLD", "IWN"]}
    # ...and what it cannot move is REPORTED, not silently dropped: measuring
    # half a revision and calling the result evidence is worse than waiting.
    assert rule_revision.unknown_parameters(spec) == ["invent_a_new_signal"]


def test_a_prose_only_revision_is_not_mechanically_testable() -> None:
    """The prose is never parsed. A `proposed_rule` sentence is LLM-authored, and
    guessing constants out of it means measuring a rule nobody proposed."""
    assert rule_revision.extract_overrides({"proposed_rule": "check the haven too"}) is None
    assert rule_revision.extract_overrides({"parameters": {}}) is None
    assert rule_revision.extract_overrides({"parameters": "not a dict"}) is None


def test_the_verdict_is_pareto_over_every_indicator() -> None:
    """Owner decision 2026-08-09: adopt iff at least one indicator improves and
    NONE degrades.

    It was "Sortino not degraded AND max drawdown improved" — the Worker's own
    words, and the test the 2026-08-07 overlay pair was adopted under — until
    measuring the Worker's most repeated critique showed that rule could only
    ever adopt OVERLAY changes. The stack's worst drawdown is the covid crash,
    set by the 200d overlay's latency; book selection cannot move it, so
    requiring the drawdown to IMPROVE refused every book-selection revision
    without the test being able to say why.

    Rule #1 survives intact — nothing may get worse."""
    # the shape of the adopted 2026-08-07 change: several improve, none degrade
    assert _measurement(sortino=(1.09, 1.17), drawdown=(-0.238, -0.206)).adopt is True
    # drawdown improves but Sortino degrades -> refused. Pareto is not a trade.
    assert _measurement(sortino=(1.17, 1.09), drawdown=(-0.238, -0.206)).adopt is False
    # THE CASE THAT CHANGED, and the reason it did: return improves at UNCHANGED
    # risk. The old rule refused this; it is the velocity veto's exact shape.
    assert (
        _measurement(
            sortino=(1.173, 1.244),
            drawdown=(-0.2061, -0.2061),
            cagr=(0.1072, 0.1110),
            calmar=(0.52, 0.54),
        ).adopt
        is True
    )
    # ...and buying that return with drawdown is still refused on the spot
    assert (
        _measurement(sortino=(1.09, 1.30), drawdown=(-0.206, -0.238), cagr=(0.10, 0.13)).adopt
        is False
    )
    # a change that moves nothing at all is not an adoption
    assert _measurement(sortino=(1.09, 1.09), drawdown=(-0.238, -0.238)).adopt is False
    # FLOAT NOISE IS NOT A DEGRADATION, and this is the exact measurement that
    # forced the tolerance: the velocity veto reached the SAME trough by a
    # different arithmetic path, -0.2061245891298571 vs -0.20612458912985732,
    # and an exact comparison refused the revision on -2.2e-16. Unfixed, the
    # rule refuses everything — every change perturbs every indicator at
    # machine epsilon.
    assert (
        _measurement(
            sortino=(1.173, 1.244),
            drawdown=(-0.2061245891298571, -0.20612458912985732),
            cagr=(0.1072, 0.1110),
        ).adopt
        is True
    )
    # ...and a real move of the same indicator is still caught. 1e-9 sits six
    # orders below the smallest decision-relevant change, so the gap is wide
    # enough that no plausible retuning of it flips a verdict.
    assert (
        _measurement(
            sortino=(1.173, 1.244), drawdown=(-0.2061, -0.2062), cagr=(0.1072, 0.1110)
        ).adopt
        is False
    )
    # a single indicator improving, alone, is enough when nothing else moves
    assert _measurement(sortino=(1.09, 1.09), drawdown=(-0.238, -0.206)).adopt is True
    # and CALMAR counts like the rest — it is not a derived afterthought here
    assert (
        _measurement(sortino=(1.09, 1.09), drawdown=(-0.238, -0.238), calmar=(1.0, 0.9)).adopt
        is False
    )


def test_deltas_state_the_improvement_direction() -> None:
    m = _measurement(sortino=(1.09, 1.17), drawdown=(-0.238, -0.206))
    assert m.sortino_delta == pytest.approx(0.08)
    # POSITIVE means shallower — both figures are negative fractions, so the
    # sign is worth pinning rather than leaving each caller to derive it.
    assert m.drawdown_delta == pytest.approx(0.032)
    assert "ADOPT" in rule_revision.render(m)


def test_a_missing_metric_is_unmeasurable_not_a_rejection() -> None:
    m = rule_revision.RevisionMeasurement(
        overrides={},
        baseline=NavMetrics(cagr=None, sortino=None, calmar=None, max_drawdown=None),
        variant=NavMetrics(cagr=0.1, sortino=1.0, calmar=None, max_drawdown=-0.2),
        baseline_turnover=0.0,
        variant_turnover=0.0,
    )
    assert m.adopt is None  # not False — nothing was compared
    assert "unmeasurable" in rule_revision.render(m)


def test_every_registered_knob_exists_on_the_module_it_overrides() -> None:
    """The registry names module attributes by string. A renamed constant would
    otherwise fail at measurement time, inside a run that costs 35 years of
    backtest — and read as a broken revision rather than a broken registry."""
    for attr in rule_revision.TESTABLE_PARAMETERS.values():
        assert hasattr(market_signal, attr), attr
