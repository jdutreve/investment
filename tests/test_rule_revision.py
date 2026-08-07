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
    *, sortino: tuple[float, float], drawdown: tuple[float, float]
) -> rule_revision.RevisionMeasurement:
    def metrics(s: float, d: float) -> NavMetrics:
        return NavMetrics(cagr=0.10, sortino=s, calmar=None, max_drawdown=d)

    return rule_revision.RevisionMeasurement(
        overrides={"confirm_decisions": 4},
        baseline=metrics(sortino[0], drawdown[0]),
        variant=metrics(sortino[1], drawdown[1]),
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


def test_the_verdict_is_the_workers_own_acceptance_test() -> None:
    """ "Adopt only if it does not degrade Sortino and improves maxDD" — written
    by the proposing Worker, unprompted and repeatedly, and the rule under which
    the 2026-08-07 overlay pair was adopted. Note the asymmetry: Sortino may
    hold flat, the drawdown must actually improve. That is rule #1 as a test."""
    # both improve -> adopt (the shape of the adopted 2026-08-07 change)
    assert _measurement(sortino=(1.09, 1.17), drawdown=(-0.238, -0.206)).adopt is True
    # drawdown improves but Sortino degrades -> no
    assert _measurement(sortino=(1.17, 1.09), drawdown=(-0.238, -0.206)).adopt is False
    # Sortino improves but the drawdown does not -> no, and this is the case the
    # asymmetry exists for: a return-flattering change that buys no safety
    assert _measurement(sortino=(1.09, 1.30), drawdown=(-0.238, -0.238)).adopt is False
    # flat Sortino with a shallower drawdown is enough
    assert _measurement(sortino=(1.09, 1.09), drawdown=(-0.238, -0.206)).adopt is True


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
