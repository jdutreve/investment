"""M5 unit tests (docs/MILESTONES.md M5 Definition of Verified) — pure
functions in `mechanical/invariants.py` only, no DB. `test_confrontation_
fixture_moves_weight_by_hand` is the named M5 DoV item: "an active-condition
invariant whose effect beats its benchmark (by method) moves a
weight_effective as computed by hand"."""

import dataclasses
import itertools
import math

import pandas as pd
import pytest

from investment.mechanical import invariants


def _empty_registries() -> invariants.Registries:
    return invariants.Registries(
        signals=set(), asset_classes=set(), strategies=set(), assets=set(), regime_types=set()
    )


# -- weight formula (CLAUDE.md "Invariant weight model") -------------------


def test_market_score_defaults_to_one_before_any_confrontation() -> None:
    assert invariants.market_score(0, 0) == 1.0


def test_market_score_is_confirmation_ratio() -> None:
    assert invariants.market_score(3, 1) == pytest.approx(0.75)


def test_recency_factor_decays_from_one_toward_half() -> None:
    assert invariants.recency_factor(0, 365.0) == pytest.approx(1.0)
    # A very stale condition asymptotically approaches 0.5, never below it.
    assert invariants.recency_factor(10_000_000, 365.0) == pytest.approx(0.5, abs=1e-6)


def test_weight_effective_never_drops_below_floor() -> None:
    assert invariants.weight_effective(0.85, 0.0, 0.5, 0.40) == pytest.approx(0.40)


def test_confrontation_fixture_moves_weight_by_hand() -> None:
    """M5 DoV: an active-condition invariant whose effect beats its
    benchmark (by method) moves weight_effective as computed by hand.
    inv-inflation-persistence-tips: weight_initial=0.85, floor=0.40
    (dalio tier); 4 confirmations, 1 infirmation, condition active NOW
    (days_since=0) -> recency=1.0."""
    weight_initial, floor_weight = 0.85, 0.40
    confirmations, infirmations = 4, 1
    score, recency, w_eff = invariants.compute_weight_update(
        weight_initial,
        floor_weight,
        confirmations,
        infirmations,
        days_since=0,
        half_life_days=365.0,
    )
    # By hand: score=4/5=0.8; recency=1.0 (active now); weight=max(0.85*0.8*1.0, 0.40)=0.68.
    assert score == pytest.approx(0.8)
    assert recency == pytest.approx(1.0)
    assert w_eff == pytest.approx(0.68)


def _verdict(confirmations: int, infirmations: int) -> str:
    total = confirmations + infirmations
    score = confirmations / total if total else 1.0
    return invariants.time_validation_verdict(
        confirmations, infirmations, score, 3.0, 0.60, 4.0, 0.35, 0.95, 0.50
    )


def test_time_validation_verdict_integrated_needs_effect_AND_evidence() -> None:
    """Clearing theta is necessary, never sufficient (ADR-006 M5-bis
    amendment). 5/5 is the smallest perfect record a coin does not
    reproduce at 5% (0.5^5 = 0.031)."""
    assert _verdict(5, 0) == "integrated"
    assert _verdict(53, 29) == "integrated"  # the real gold invariant, tail 0.005
    assert _verdict(4, 0) == "proposed"  # 1.000 but a coin does this 6.3% of the time
    assert _verdict(2, 0) == "proposed"  # below N_min
    assert _verdict(2, 1) == "proposed"  # 0.667 >= theta, and a literal coin flip


def test_verdict_point_test_alone_would_integrate_noise() -> None:
    """The defect the tail test closes: at small N, `score >= theta` is
    cleared by chance alone. Every case here passes theta and is refused —
    with the probability a ZERO-edge invariant produces it (see
    `binomial_tail_at_least` against the 0.50 null)."""
    for c, i, p_noise in ((2, 1, 0.500), (3, 0, 0.125), (9, 5, 0.212), (12, 8, 0.252)):
        total = c + i
        assert c / total >= 0.60  # would have been 'integrated' under the old rule
        assert invariants.binomial_tail_at_least(c, total, 0.50) == pytest.approx(p_noise, abs=1e-3)
        assert _verdict(c, i) == "proposed"


def test_time_validation_verdict_rejected_when_refuted() -> None:
    # 5 confrontations, score 0.20 < refuted_score 0.35, total >= refuted_min 4.
    assert _verdict(1, 4) == "rejected"


def test_binomial_tail_golden_values() -> None:
    """Hand-checkable: 5 fair coins all landing heads is 0.5^5; 3 of 3 is
    0.5^3; the two tails of a symmetric null at c = n/2 overlap on the
    median term, so they sum to 1 + P(X = n/2)."""
    assert invariants.binomial_tail_at_least(5, 5, 0.50) == pytest.approx(0.03125)
    assert invariants.binomial_tail_at_least(3, 3, 0.50) == pytest.approx(0.125)
    assert invariants.binomial_tail_at_least(0, 10, 0.50) == pytest.approx(1.0)
    assert invariants.binomial_tail_at_most(10, 10, 0.50) == pytest.approx(1.0)
    assert invariants.binomial_tail_at_least(0, 0, 0.50) == 1.0
    assert invariants.binomial_tail_at_most(0, 0, 0.50) == 1.0
    both = invariants.binomial_tail_at_least(5, 10, 0.50) + invariants.binomial_tail_at_most(
        5, 10, 0.50
    )
    assert both == pytest.approx(1.0 + math.comb(10, 5) * 0.5**10)
    # Monotone in evidence at a fixed rate: a longer perfect run is rarer.
    assert invariants.binomial_tail_at_least(20, 20, 0.50) < invariants.binomial_tail_at_least(
        10, 10, 0.50
    )


def test_verdict_dead_middle_rejects_on_confidence_at_large_n() -> None:
    """ADR-006 amendment: 'Nothing stays proposed forever'. A score in the
    0.35..theta dead middle used to stay 'proposed' at ANY N. Now, once a
    true rate of theta becomes an implausible source of evidence this bad,
    the invariant is REJECTED as demonstrably unable to reach the bar — the
    real liquidity-easing case (0.545 on N=354) qualifies instead of
    stalling."""
    assert _verdict(193, 161) == "rejected"  # 0.545, N=354 — amply measured
    assert _verdict(50, 50) == "rejected"  # 0.500, N=100 — no edge, amply measured


def test_verdict_dead_middle_stays_proposed_while_genuinely_unresolved() -> None:
    """The same mid-band score with small N keeps 'proposed' — theta is still
    a plausible source of the evidence, so it is genuinely insufficient (the
    ONLY remaining meaning of 'proposed')."""
    assert _verdict(30, 30) == "proposed"  # 0.500, N=60 — theta-tail 0.075
    assert _verdict(2, 2) == "proposed"  # 0.500, N=4


def test_verdict_inadequate_rejection_cannot_race_integration() -> None:
    """score >= theta puts the count at or above theta's own median, so its
    lower tail is ~0.5 and can never fall under alpha: 'integrated' and
    'inadequate' are mutually exclusive by construction, whatever the N."""
    for c, i in ((3, 0), (60, 40), (7, 3), (240, 160), (5, 0), (53, 29)):
        total = c + i
        if c / total >= 0.60:
            assert invariants.binomial_tail_at_most(c, total, 0.60) > 0.05
            assert _verdict(c, i) in ("integrated", "proposed")


def test_verdict_evidence_eventually_settles_every_true_rate() -> None:
    """'Nothing stays proposed forever' (ADR-006), now against BOTH bars: a
    true rate either side of theta resolves once enough moments accrue, and
    only the measure-zero rate exactly AT theta is allowed to stall."""
    for true_rate, expected in ((0.70, "integrated"), (0.50, "rejected"), (0.30, "rejected")):
        c = round(true_rate * 400)
        assert _verdict(c, 400 - c) == expected


# -- condition / moment evaluation ------------------------------------------


def test_confront_moment_outperform_and_underperform() -> None:
    assert invariants.confront_moment(0.10, 0.02, "outperform", 0.05) == "confirmed"
    assert invariants.confront_moment(-0.05, 0.02, "outperform", 0.05) == "refuted"
    assert invariants.confront_moment(0.015, 0.02, "outperform", 0.05) is None  # within margin
    assert invariants.confront_moment(-0.20, -0.05, "underperform", 0.05) == "confirmed"
    assert invariants.confront_moment(0.10, -0.05, "underperform", 0.05) == "refuted"


def test_confront_moment_none_on_missing_data() -> None:
    assert invariants.confront_moment(None, 0.02, "outperform", 0.05) is None
    assert invariants.confront_moment(0.02, None, "outperform", 0.05) is None


# -- forward window (the M5 verification fix) --------------------------------


def _frame(values: dict[str, list[float]], idx: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(values, index=idx)


def test_asof_forward_reads_the_window_after_the_moment_not_before() -> None:
    """The defect this fixes: reading the metric AT the moment scores it on a
    TRAILING window that predates the condition. `_asof_forward` must read the
    row one horizon LATER (whose trailing window is the moment's forward
    window)."""
    idx = pd.date_range("2020-01-03", periods=30, freq="W-FRI")
    # 'return' rises over time, so the value at the moment and the value one
    # horizon later are unambiguously different.
    frame = _frame({"return": [float(i) for i in range(30)]}, idx)
    moment = idx[5]
    horizon = pd.Timedelta(weeks=12)

    at_moment = invariants._asof(frame, "return", moment)
    forward = invariants._asof_forward(frame, "return", moment, horizon)

    assert at_moment == pytest.approx(5.0)
    # 12 weeks after idx[5] is idx[17] -> value 17.0, NOT 5.0.
    assert forward == pytest.approx(17.0)


def test_asof_forward_none_when_outcome_window_incomplete() -> None:
    """ARCHITECTURE confronts a moment only "when it COMPLETES" — a moment
    whose forward window runs past the end of the data must be a no-op, not a
    verdict scored on a truncated window."""
    # 20 weekly rows span 19 weeks, so a 12w horizon is complete for the early
    # rows and runs off the end for the late ones.
    idx = pd.date_range("2020-01-03", periods=20, freq="W-FRI")
    frame = _frame({"return": [float(i) for i in range(20)]}, idx)
    horizon = pd.Timedelta(weeks=12)
    # idx[18] + 12w lands well beyond idx[-1] -> incomplete.
    assert invariants._asof_forward(frame, "return", idx[18], horizon) is None
    # idx[0] + 12w = idx[12], covered -> a real value.
    assert invariants._asof_forward(frame, "return", idx[0], horizon) == pytest.approx(12.0)


def test_median_asof_forward_ignores_incomplete_series() -> None:
    long_idx = pd.date_range("2020-01-03", periods=30, freq="W-FRI")
    short_idx = pd.date_range("2020-01-03", periods=6, freq="W-FRI")
    others = {
        "a": _frame({"return": [1.0] * 30}, long_idx),
        "b": _frame({"return": [3.0] * 30}, long_idx),
        "c": _frame({"return": [99.0] * 6}, short_idx),  # too short -> excluded
    }
    median = invariants._median_asof_forward(others, "return", long_idx[0], pd.Timedelta(weeks=12))
    assert median == pytest.approx(2.0)  # median(1, 3), 'c' excluded


# -- baseline-relative confrontation ------------------------------------------


def test_baseline_excess_is_median_of_the_no_condition_excess() -> None:
    condition = [{"signal": "growth", "feature": "speed", "op": ">", "value": 0}]
    assert invariants.baseline_excess([0.01, 0.05, 0.09], condition) == pytest.approx(0.05)


def test_baseline_excess_is_zero_for_an_always_condition() -> None:
    """'always' makes no conditional claim, so its lift is zero by
    construction — lift-scoring would pin it at 0.50 forever. Its claim IS
    absolute, so it keeps an absolute (zero-baseline) measure."""
    assert invariants.baseline_excess([0.20, 0.30, 0.40], []) == pytest.approx(0.0)


def test_baseline_excess_empty_sample_falls_back_to_absolute() -> None:
    condition = [{"signal": "growth", "feature": "speed", "op": ">", "value": 0}]
    assert invariants.baseline_excess([], condition) == pytest.approx(0.0)


def test_a_condition_matching_the_base_rate_scores_near_the_null() -> None:
    """THE defect this fixes, as a unit. A handle that beats its benchmark by
    a steady +0.20 does so whether or not the condition holds. Measured
    absolutely every moment confirms (score 1.0 -> integrated); measured
    against its own baseline nothing resolves, so the condition earns no
    credit for the handle's standing advantage."""
    margin = 0.02
    steady_excess = [0.20] * 20  # same excess at every date, condition or not
    condition = [{"signal": "growth", "feature": "speed", "op": ">", "value": 0}]

    absolute = [invariants.confront_moment(e, 0.0, "outperform", margin) for e in steady_excess]
    assert absolute.count("confirmed") == 20  # certifies the base rate

    baseline = invariants.baseline_excess(steady_excess, condition)
    relative = [
        invariants.confront_moment(e, baseline, "outperform", margin) for e in steady_excess
    ]
    assert relative.count("confirmed") == 0
    assert relative.count("refuted") == 0
    assert all(v is None for v in relative)  # no skill shown => no verdict


def test_a_condition_with_real_lift_still_confirms() -> None:
    """The mirror: a condition that genuinely lifts the handle above its own
    baseline must still be confirmable — the fix must not make everything
    unconfirmable."""
    margin = 0.02
    # Handle usually delivers ~0.00 excess; at condition-moments it delivers +0.20.
    all_dates_excess = [0.0] * 18 + [0.20, 0.20]
    condition = [{"signal": "growth", "feature": "speed", "op": ">", "value": 0}]
    baseline = invariants.baseline_excess(all_dates_excess, condition)

    assert baseline == pytest.approx(0.0)
    assert invariants.confront_moment(0.20, baseline, "outperform", margin) == "confirmed"


def test_underperform_direction_is_measured_against_the_same_baseline() -> None:
    """De-rigging: with a +0.20 standing advantage, an 'underperform' claim
    was refuted automatically. Against the baseline it is judged on whether
    the condition actually dented the handle."""
    margin = 0.02
    baseline = 0.20
    # At the moment the handle still beat the benchmark (+0.05) but by far
    # LESS than it usually does -> the condition really did pressure it.
    assert invariants.confront_moment(0.05, baseline, "underperform", margin) == "confirmed"
    # Absolutely, that same moment looks like an outperformance -> refuted.
    assert invariants.confront_moment(0.05, 0.0, "underperform", margin) == "refuted"


# -- per-metric margins -------------------------------------------------------


def test_margin_for_metric_uses_override_then_falls_back() -> None:
    thresholds = {"confrontation_margin": 0.10, "confrontation_margin_max_drawdown": 0.01}
    assert invariants.margin_for_metric("max_drawdown", thresholds) == pytest.approx(0.01)
    assert invariants.margin_for_metric("return", thresholds) == pytest.approx(0.10)


def test_max_drawdown_margin_admits_realistic_strategy_gaps() -> None:
    """Regression on the real numbers: four-seasons-rp's max_drawdown differs
    from the median of the other strategies by at most ~0.04 over 35y, so the
    generic 0.10 band made EVERY moment a no-op and the invariant
    unmaturable. The seeded per-metric band must let a gap that size resolve."""
    from investment.db.seed_data import SYSTEM_THRESHOLDS

    margin = invariants.margin_for_metric("max_drawdown", SYSTEM_THRESHOLDS)
    observed_gap = -0.0414  # measured against the live 35y DB at M5 verification
    assert abs(observed_gap) > margin
    assert invariants.confront_moment(-0.10, -0.0586, "outperform", margin) == "refuted"


def test_evaluate_condition_ands_predicates_over_aligned_frames() -> None:
    idx = pd.date_range("2020-01-01", periods=6, freq="D")
    inflation = pd.DataFrame(
        {"level": [3.0, 3.0, 3.0, 1.0, 1.0, 1.0], "speed": [0.1] * 6, "acceleration": [0.0] * 6},
        index=idx,
    )
    condition = [
        {"signal": "inflation", "feature": "level", "op": ">", "value": 2.5},
        {"signal": "inflation", "feature": "speed", "op": ">", "value": 0},
    ]
    active = invariants.evaluate_condition(
        condition, {"inflation": inflation}, pd.Series(dtype=object)
    )
    assert list(active) == [True, True, True, False, False, False]


def test_sample_moments_empty_series() -> None:
    assert invariants.sample_moments(pd.Series(dtype=bool), pd.Timedelta(weeks=12)) == []


def test_sample_moments_takes_short_episode_starts() -> None:
    """A short episode still contributes its START (the decision moment),
    because the next active day within a horizon is skipped."""
    idx = pd.date_range("2020-01-01", periods=8, freq="D")
    active = pd.Series([True, True, False, False, True, True, True, False], index=idx)
    # Horizon far longer than the gap between the two bursts -> only the
    # first burst's start survives.
    assert invariants.sample_moments(active, pd.Timedelta(weeks=12)) == [idx[0]]
    # Horizon shorter than the gap -> each burst contributes its start.
    assert invariants.sample_moments(active, pd.Timedelta(days=3)) == [idx[0], idx[4]]


def test_sample_moments_spaces_a_long_episode_by_horizon() -> None:
    """THE fix behind the gold verdict: a LONG episode is sampled THROUGHOUT
    at horizon spacing, not collapsed to its first day. `real_rate < 2.5`
    holds for one 7050-day block (2001-2020) — per-episode scoring gave that
    whole era ONE data point and let 1990s threshold chatter carry the
    verdict (0.158/refuted on N=19 vs 0.542/undecided on N=107)."""
    idx = pd.date_range("2020-01-01", periods=365, freq="D")
    active = pd.Series(True, index=idx)
    moments = invariants.sample_moments(active, pd.Timedelta(weeks=12))
    assert len(moments) == 5  # 365d / 84d, walking forward
    assert moments[0] == idx[0]
    # Non-overlapping: consecutive outcome windows never share a day, which
    # is what the Wilson bound in the verdict assumes.
    gaps = [(b - a).days for a, b in itertools.pairwise(moments)]
    assert all(g >= 84 for g in gaps)


def test_sample_moments_is_continuous_in_condition_frequency() -> None:
    """No cliff between 'nearly always' and 'always' — the discontinuity that
    made an 88%-true condition untestable (1 moment) while a 100%-true one
    got ~1800."""
    idx = pd.date_range("2020-01-01", periods=365, freq="D")
    horizon = pd.Timedelta(weeks=12)
    always = pd.Series(True, index=idx)
    nearly = pd.Series(True, index=idx)
    nearly.iloc[180] = False  # one day off in the middle
    assert len(invariants.sample_moments(nearly, horizon)) == len(
        invariants.sample_moments(always, horizon)
    )


# -- VALIDATION GATE ---------------------------------------------------------


def _seed_registries() -> invariants.Registries:
    """Built from the real seed constants, so the gate is exercised against
    the actual vocabulary rather than a fixture that can drift from it."""
    from investment.db.seed_data import (
        ALLOWED_TICKERS,
        BENCHMARK_CLASSES,
        REGIME_TYPES,
        SIGNAL_ALIASES,
        STRATEGIES,
    )

    fine_to_coarse = {f for fines in BENCHMARK_CLASSES.values() for f in fines}
    assets = {str(t["ticker"]) for t in ALLOWED_TICKERS if t["asset_class"] in fine_to_coarse}
    assets.add("cash")
    return invariants.Registries(
        signals=set(SIGNAL_ALIASES),
        asset_classes=set(BENCHMARK_CLASSES),
        strategies={str(s["id"]) for s in STRATEGIES},
        assets=assets,
        regime_types={str(r["id"]) for r in REGIME_TYPES},
    )


def test_validate_invariant_accepts_every_seed_invariant() -> None:
    """Every seed invariant (db/seed_data.py INVARIANTS) must clear the
    mechanical gate — a real regression on the actual seed data, not a
    synthetic fixture. Counted from the constant, so adding an invariant to
    the philosophy extends the check instead of breaking it."""
    from investment.db.seed_data import INVARIANTS

    registries = _seed_registries()
    for inv in INVARIANTS:
        reason = invariants.validate_invariant(inv["condition"], inv["effect"], registries)
        assert reason is None, f"{inv['id']}: {reason}"


def test_validate_invariant_rejects_unknown_signal() -> None:
    reason = invariants.validate_invariant(
        [{"signal": "moon_phase", "feature": "level", "op": ">", "value": 1}],
        None,
        _empty_registries(),
    )
    assert reason is not None and "moon_phase" in reason


def test_validate_invariant_rejects_hyphenated_signal_alias() -> None:
    """The registry key is `real_rate`; 'real-yield' is a plausible-looking
    near-miss that must DEMOTE rather than silently resolve (it arrived that
    way in the owner-submitted gold invariant)."""
    reason = invariants.validate_invariant(
        [{"signal": "real-yield", "feature": "level", "op": "<", "value": 2.5}],
        None,
        _seed_registries(),
    )
    assert reason is not None and "real-yield" in reason


def test_validate_invariant_rejects_method_handle_mismatch() -> None:
    reason = invariants.validate_invariant(
        [],
        {
            "handle": "asset-class:equities",
            "metric": "return",
            "method": "cross_strategy",
            "direction": "outperform",
        },
        dataclasses.replace(_empty_registries(), asset_classes={"equities"}),
    )
    assert reason is not None


def test_validate_invariant_accepts_asset_handle_with_cross_class() -> None:
    """docs/ARCHITECTURE.md VALIDATION GATE: "cross_class ⇒ asset/class
    handle". An asset handle with cross_class is LEGAL — the engine wrongly
    demanded 'absolute' until the gold invariant exercised it."""
    effect = {
        "handle": "asset:GLD",
        "metric": "return",
        "method": "cross_class",
        "direction": "outperform",
    }
    assert invariants.validate_invariant([], effect, _seed_registries()) is None


def test_validate_invariant_rejects_uncomputed_metric() -> None:
    """'relative_return' arrived TWICE from a real author. It is plausible
    but not a computed indicator, and the gate let it through until an
    owner-submitted invariant exposed it: the confrontation reads `metric` as
    a benchmark-frame COLUMN, so it raised KeyError mid-sweep instead of
    demoting — the one thing the gate exists to prevent. (The relativity is
    the METHOD's job — cross_class — not the metric's.)"""
    effect = {
        "handle": "asset:GLD",
        "metric": "relative_return",
        "method": "cross_class",
        "direction": "outperform",
    }
    reason = invariants.validate_invariant([], effect, _seed_registries())
    assert reason is not None and "relative_return" in reason


def test_validate_invariant_accepts_every_computed_metric() -> None:
    from investment.mechanical.backtests import BENCHMARK_METRICS

    for metric in BENCHMARK_METRICS:
        effect = {
            "handle": "asset-class:equities",
            "metric": metric,
            "method": "cross_class",
            "direction": "outperform",
        }
        assert invariants.validate_invariant([], effect, _seed_registries()) is None, metric


def test_validate_invariant_rejects_type_feature_on_a_series_signal() -> None:
    """'feature valid FOR IT' (spec), not globally: a market series has no
    'type' column, so this reached the sweep as a KeyError instead of
    demoting — the same shape as the `relative_return` hole."""
    condition = [{"signal": "real_yield", "feature": "type", "op": "==", "value": "x"}]
    reason = invariants.validate_invariant(condition, None, _seed_registries())
    assert reason is not None and "type" in reason


def test_validate_invariant_rejects_non_numeric_threshold() -> None:
    """'op/value type-consistent' (spec): level/speed/acceleration are floats,
    so a string threshold raised TypeError inside the comparison."""
    condition = [{"signal": "real_yield", "feature": "level", "op": "<", "value": "low"}]
    reason = invariants.validate_invariant(condition, None, _seed_registries())
    assert reason is not None and "non-numeric" in reason


def test_validate_invariant_rejects_bool_threshold() -> None:
    """`bool` is an int subclass in Python, so a naive numeric check admits
    it — and `speed < True` is never what an author meant."""
    condition = [{"signal": "growth", "feature": "speed", "op": "<", "value": True}]
    assert invariants.validate_invariant(condition, None, _seed_registries()) is not None


def test_validate_invariant_rejects_unknown_regime_type() -> None:
    """Worse than a crash: an unknown RegimeType id silently never matches,
    so the invariant is unmaturable for want of moments rather than
    demoted."""
    condition = [{"signal": "regime", "feature": "type", "op": "==", "value": "not-a-regime"}]
    reason = invariants.validate_invariant(condition, None, _seed_registries())
    assert reason is not None and "not-a-regime" in reason


def test_validate_invariant_accepts_a_real_regime_type() -> None:
    condition = [
        {
            "signal": "regime",
            "feature": "type",
            "op": "==",
            "value": "falling-growth-rising-inflation",
        }
    ]
    assert invariants.validate_invariant(condition, None, _seed_registries()) is None


def test_validate_invariant_accepts_both_real_rate_signals() -> None:
    """Q: can invariants be specified with real_rate OR real_yield? Both are
    registry signals over level/speed/acceleration, and the gate admits
    either — they are distinct economics (policy stance vs cost of capital),
    not variants (docs/ARCHITECTURE.md CURATOR RULE 2)."""
    effect = {
        "handle": "asset:GLD",
        "metric": "return",
        "method": "cross_class",
        "direction": "outperform",
    }
    for signal in ("real_rate", "real_yield"):
        for feature in ("level", "speed", "acceleration"):
            condition = [{"signal": signal, "feature": feature, "op": "<", "value": 2.5}]
            reason = invariants.validate_invariant(condition, effect, _seed_registries())
            assert reason is None, f"{signal}.{feature}: {reason}"


def test_validate_invariant_rejects_unknown_asset() -> None:
    effect = {
        "handle": "asset:DOGE",
        "metric": "return",
        "method": "cross_class",
        "direction": "outperform",
    }
    reason = invariants.validate_invariant([], effect, _seed_registries())
    assert reason is not None and "DOGE" in reason


# -- contradiction check ------------------------------------------------------


def test_conditions_can_overlap_always_overlaps_everything() -> None:
    assert invariants.conditions_can_overlap(
        [], [{"signal": "x", "feature": "level", "op": ">", "value": 0}]
    )


def test_conditions_can_overlap_disjoint_strict_signs() -> None:
    a = [{"signal": "growth", "feature": "speed", "op": "<", "value": 0}]
    b = [{"signal": "growth", "feature": "speed", "op": ">", "value": 0}]
    assert not invariants.conditions_can_overlap(a, b)


def test_conditions_can_overlap_different_signals_assumed_independent() -> None:
    a = [{"signal": "growth", "feature": "speed", "op": "<", "value": 0}]
    b = [{"signal": "liquidity", "feature": "speed", "op": ">", "value": 0}]
    assert invariants.conditions_can_overlap(a, b)


def test_find_contradictions_flags_opposing_effects_on_same_handle() -> None:
    eff_up = {
        "handle": "asset-class:equities",
        "metric": "return",
        "method": "cross_class",
        "direction": "outperform",
    }
    eff_down = {
        "handle": "asset-class:equities",
        "metric": "return",
        "method": "cross_class",
        "direction": "underperform",
    }
    invariants_list = [
        ("inv-a", [], eff_up),
        ("inv-b", [], eff_down),
        ("inv-c", [], {**eff_up, "handle": "asset-class:bonds"}),  # different handle, no flag
    ]
    pairs = invariants.find_contradictions(invariants_list)
    assert len(pairs) == 1
    assert {pairs[0].invariant_a, pairs[0].invariant_b} == {"inv-a", "inv-b"}


def test_find_contradictions_no_flag_when_conditions_cannot_overlap() -> None:
    eff_up = {
        "handle": "asset-class:equities",
        "metric": "return",
        "method": "cross_class",
        "direction": "outperform",
    }
    eff_down = {
        "handle": "asset-class:equities",
        "metric": "return",
        "method": "cross_class",
        "direction": "underperform",
    }
    a_cond = [{"signal": "growth", "feature": "speed", "op": "<", "value": 0}]
    b_cond = [{"signal": "growth", "feature": "speed", "op": ">", "value": 0}]
    pairs = invariants.find_contradictions([("inv-a", a_cond, eff_up), ("inv-b", b_cond, eff_down)])
    assert pairs == []
