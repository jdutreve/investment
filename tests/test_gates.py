"""M6 unit tests for the deterministic proposal gates (docs/USE_CASES.md UC8-A
switch / UC8-B reallocation) — pure functions in `mechanical/gates.py`, no DB.
"""

import pytest

from investment.mechanical.gates import (
    Caps,
    ProposalThresholds,
    blend_allocation,
    concentration_ok,
    drawdown_ok,
    max_allocation_change_pts,
    reallocation_gates,
    switch_gates,
    turnover_pct,
)
from investment.mechanical.snapshots import RankedRow, ValuationRow

CAPS = Caps(max_single_asset_pct=40.0, max_drawdown_pct=-15.0)
THRESHOLDS = ProposalThresholds(
    sortino_gap_min=0.02,
    calmar_min=1.5,
    min_allocation_change_pts=5.0,
    max_turnover_pct=30.0,
    blend_scenario_weight=0.4,
    blend_favors_weight=0.6,
)


def _ranked(
    portfolio_id: str,
    *,
    rank: int,
    defender: bool = False,
    sortino: float | None = 1.0,
    calmar: float | None = 2.0,
    max_drawdown: float | None = -0.10,
    allocation: dict[str, float] | None = None,
    gap: float | None = None,
) -> RankedRow:
    row = ValuationRow(
        portfolio_id=portfolio_id,
        defender=defender,
        framework_id="4seasons",
        designed_regime_type_id=None,
        primary_strategy_id="four-seasons-rp",
        allocation=allocation or {"SPY": 30.0, "TLT": 30.0, "IEF": 20.0, "GLD": 20.0},
        sharpe_rolling=sortino,
        sortino_rolling=sortino,
        calmar_rolling=calmar,
        max_drawdown=max_drawdown,
        volatility=None,
        return_3m=None,
        return_6m=None,
        return_1y=None,
        return_3y=None,
        return_5y=None,
    )
    return RankedRow(
        row=row,
        rank=rank,
        gap_to_defender=None if defender else {"sortino_rolling": gap},
    )


# -- binding caps (CLAUDE.md "Binding caps") -------------------------------


def test_concentration_cap_is_inclusive_at_the_limit() -> None:
    # 4s-rising-growth-equities really is seeded at SPY 40 against a 40 cap —
    # an exclusive test would silently make a seeded portfolio ineligible.
    assert concentration_ok({"SPY": 40.0, "TLT": 35.0, "IEF": 25.0}, CAPS)
    assert not concentration_ok({"SPY": 40.01, "TLT": 34.99, "IEF": 25.0}, CAPS)


def test_drawdown_rule_compares_fraction_against_percent_points() -> None:
    """docs/DATA_MODELS.md "Units convention": `max_drawdown` is a decimal
    fraction, `max_drawdown_pct` is percent points. Reading -0.20 against
    -15.0 without converting would pass every drawdown ever recorded."""
    assert drawdown_ok(-0.14, CAPS)
    assert drawdown_ok(-0.15, CAPS)  # exactly at the rule
    assert not drawdown_ok(-0.16, CAPS)
    assert not drawdown_ok(-0.20, CAPS)


def test_missing_drawdown_does_not_breach() -> None:
    assert drawdown_ok(None, CAPS)


# -- UC8-A switch gates ----------------------------------------------------


def test_switch_gates_pass_on_a_clean_challenger() -> None:
    challenger = _ranked(
        "barbell",
        rank=1,
        gap=0.30,
        allocation={"SHY": 35.0, "cash": 30.0, "IEF": 20.0, "SPY": 15.0},
    )
    defender = _ranked("4s", rank=2, defender=True)
    assert switch_gates(challenger, defender, CAPS, THRESHOLDS).passed


def test_gate_1_refuses_a_challenger_ranked_below_the_defender() -> None:
    challenger = _ranked("barbell", rank=3, gap=0.30)
    defender = _ranked("4s", rank=1, defender=True)
    outcome = switch_gates(challenger, defender, CAPS, THRESHOLDS)
    assert not outcome.passed
    assert outcome.failed_gate == "outranks_defender"


def test_gate_2_refuses_a_sortino_gap_below_the_floor() -> None:
    challenger = _ranked("barbell", rank=1, gap=0.01)
    defender = _ranked("4s", rank=2, defender=True)
    assert switch_gates(challenger, defender, CAPS, THRESHOLDS).failed_gate == "sortino_gap_min"


def test_gate_3_calmar_is_absolute_not_relative_to_the_defender() -> None:
    """UC8-A: "compared to the threshold, not to the defender's Calmar" — a
    challenger may pass with a WORSE Calmar than the defender."""
    barbell = {"SHY": 35.0, "cash": 30.0, "IEF": 20.0, "SPY": 15.0}
    challenger = _ranked("barbell", rank=1, gap=0.30, calmar=1.6, allocation=barbell)
    defender = _ranked("4s", rank=2, defender=True, calmar=9.0)
    assert switch_gates(challenger, defender, CAPS, THRESHOLDS).passed

    weak = _ranked("barbell", rank=1, gap=0.30, calmar=1.4, allocation=barbell)
    assert switch_gates(weak, defender, CAPS, THRESHOLDS).failed_gate == "calmar_min"


def test_gate_4_refuses_a_drawdown_rule_breach() -> None:
    """CLAUDE.md: breaching the user drawdown rule "keeps the row ranked but
    excludes it from defender role and proposal candidacy"."""
    challenger = _ranked("barbell", rank=1, gap=0.30, max_drawdown=-0.22)
    defender = _ranked("4s", rank=2, defender=True)
    assert switch_gates(challenger, defender, CAPS, THRESHOLDS).failed_gate == "max_drawdown_pct"


def test_gate_4_refuses_a_concentration_breach() -> None:
    challenger = _ranked("concentrated", rank=1, gap=0.30, allocation={"SPY": 70.0, "cash": 30.0})
    defender = _ranked("4s", rank=2, defender=True)
    assert (
        switch_gates(challenger, defender, CAPS, THRESHOLDS).failed_gate == "max_single_asset_pct"
    )


def test_gate_5_refuses_a_cosmetic_allocation_difference() -> None:
    defender_allocation = {"SPY": 30.0, "TLT": 30.0, "IEF": 20.0, "GLD": 20.0}
    challenger = _ranked(
        "near-clone",
        rank=1,
        gap=0.30,
        allocation={"SPY": 32.0, "TLT": 28.0, "IEF": 20.0, "GLD": 20.0},
    )
    defender = _ranked("4s", rank=2, defender=True, allocation=defender_allocation)
    assert (
        switch_gates(challenger, defender, CAPS, THRESHOLDS).failed_gate
        == "min_allocation_change_pts"
    )


def test_max_allocation_change_counts_a_dropped_ticker_in_full() -> None:
    assert max_allocation_change_pts({"SPY": 30.0, "GLD": 20.0}, {"SPY": 30.0}) == 20.0


# -- UC8-B reallocation: the blend ------------------------------------------


def test_blend_is_04_scenario_plus_06_favors_rounded_to_25() -> None:
    """docs/ARCHITECTURE.md: `delta = 0.4 x scenario_delta + 0.6 x favors_delta`,
    rounded to 2.5-point increments, then re-normalized to sum 100."""
    current = {"SPY": 50.0, "TLT": 50.0}
    scenario = {"SPY": 100.0, "TLT": 0.0}  # scenario_delta: SPY +50, TLT -50
    favors = {"SPY": 0.0, "TLT": 100.0}  # favors_delta:   SPY -50, TLT +50
    # blended delta: SPY 0.4*50 + 0.6*(-50) = -10 -> 40; TLT +10 -> 60.
    assert blend_allocation(current, scenario, favors, THRESHOLDS) == pytest.approx(
        {"SPY": 40.0, "TLT": 60.0}
    )


def test_blend_sums_to_100_after_rounding() -> None:
    current = {"SPY": 33.0, "TLT": 33.0, "GLD": 34.0}
    scenario = {"SPY": 44.0, "TLT": 28.0, "GLD": 28.0}
    blended = blend_allocation(current, scenario, None, THRESHOLDS)
    assert sum(blended.values()) == pytest.approx(100.0)


def test_blend_with_a_none_leg_uses_a_zero_delta_not_a_void() -> None:
    """docs/EXAMPLE.md Step 8: when the top-FAVORS strategy for the regime is
    already the defender's own, the blend is `0.4 x scenario_delta + 0.6 x 0`."""
    current = {"SPY": 50.0, "TLT": 50.0}
    scenario = {"SPY": 100.0, "TLT": 0.0}
    # 0.4 * +50 = +20 -> SPY 70, TLT 30.
    assert blend_allocation(current, scenario, None, THRESHOLDS) == pytest.approx(
        {"SPY": 70.0, "TLT": 30.0}
    )


def test_blend_never_returns_a_negative_sleeve() -> None:
    current = {"SPY": 5.0, "TLT": 95.0}
    scenario = {"SPY": 0.0, "TLT": 100.0}
    favors = {"SPY": 0.0, "TLT": 100.0}
    blended = blend_allocation(current, scenario, favors, THRESHOLDS)
    assert all(w >= 0.0 for w in blended.values())


# -- UC8-B reallocation: the gates ------------------------------------------

ALLOWED = frozenset({"SPY", "TLT", "IEF", "GLD", "DJP", "SHY", "cash"})


def test_reallocation_gates_pass_on_a_clean_proposal() -> None:
    current = {"SPY": 30.0, "TLT": 30.0, "IEF": 20.0, "GLD": 20.0}
    proposed = {"SPY": 22.5, "TLT": 30.0, "IEF": 20.0, "GLD": 27.5}
    assert reallocation_gates(current, proposed, CAPS, THRESHOLDS, ALLOWED).passed


def test_gate_1_refuses_an_allocation_that_does_not_sum_to_100() -> None:
    current = {"SPY": 50.0, "TLT": 50.0}
    proposed = {"SPY": 40.0, "TLT": 45.0}
    outcome = reallocation_gates(current, proposed, CAPS, THRESHOLDS, ALLOWED)
    assert outcome.failed_gate == "allocation_sums_to_100"


def test_turnover_is_the_halved_absolute_delta_sum() -> None:
    """Turnover is `sum(|delta|)/2` — a full rotation is 100%, not 200%."""
    assert turnover_pct({"SPY": 100.0}, {"TLT": 100.0}) == pytest.approx(100.0)


def test_gate_4_refuses_turnover_above_the_ceiling() -> None:
    current = {"SPY": 30.0, "TLT": 30.0, "IEF": 20.0, "GLD": 20.0}
    # Deltas -20/-20/+20/+20 -> sum|delta| 80 -> turnover 40% > the 30% ceiling,
    # while every sleeve stays inside the 40% concentration cap.
    proposed = {"SPY": 10.0, "TLT": 10.0, "IEF": 40.0, "GLD": 40.0}
    assert turnover_pct(current, proposed) == pytest.approx(40.0)
    outcome = reallocation_gates(current, proposed, CAPS, THRESHOLDS, ALLOWED)
    assert outcome.failed_gate == "max_turnover_pct"


def test_gate_5_refuses_a_ticker_outside_allowed_tickers() -> None:
    current = {"SPY": 50.0, "TLT": 50.0}
    proposed = {"SPY": 40.0, "TLT": 30.0, "DOGE": 30.0}
    outcome = reallocation_gates(current, proposed, CAPS, THRESHOLDS, ALLOWED)
    assert outcome.failed_gate == "allowed_tickers"


def test_gate_3_refuses_a_reallocation_too_small_to_matter() -> None:
    """The gate IS the replay's "should I propose?" trigger — a sub-5pt tweak
    is noise that only pays costs (see `replay._reallocation_target`)."""
    current = {"SPY": 30.0, "TLT": 30.0, "IEF": 20.0, "GLD": 20.0}
    proposed = {"SPY": 32.0, "TLT": 28.0, "IEF": 20.0, "GLD": 20.0}
    outcome = reallocation_gates(current, proposed, CAPS, THRESHOLDS, ALLOWED)
    assert outcome.failed_gate == "min_allocation_change_pts"
