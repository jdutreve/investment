"""M5 unit tests (docs/MILESTONES.md M5 Definition of Verified) — pure
functions in `mechanical/invariants.py` only, no DB. `test_confrontation_
fixture_moves_weight_by_hand` is the named M5 DoV item: "an active-condition
invariant whose effect beats its benchmark (by method) moves a
weight_effective as computed by hand"."""

import dataclasses
import itertools

import numpy as np
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


def test_weight_effective_never_drops_below_floor() -> None:
    # 0 of 100: the posterior is (0.85 x 4 + 0) / 104 = 0.033, and the floor holds.
    assert invariants.weight_effective(0.85, 0, 100, 0.40) == pytest.approx(0.40)


def test_weight_starts_at_the_prior_and_evidence_can_lift_it_above() -> None:
    """Notoriety sets where a claim starts, not where it stops — in BOTH
    directions (owner, 2026-10-03). Unmeasured, the weight is the starting
    belief; measured, it converges on the record, so a system-tier invariant
    confirmed 65 times in 100 ends far above the 0.20 it was born with. Under
    `weight_initial x market_score` it was capped at 0.13."""
    assert invariants.weight_effective(0.20, 0, 0, 0.05) == pytest.approx(0.20)
    # By hand: (0.20 x 4 + 65) / (4 + 100) = 65.8 / 104.
    assert invariants.weight_effective(0.20, 65, 35, 0.05) == pytest.approx(65.8 / 104)
    # And a high prior is taken back by a record near chance.
    assert invariants.weight_effective(0.85, 53, 47, 0.05) == pytest.approx(56.4 / 104)


def test_confrontation_fixture_moves_weight_by_hand() -> None:
    """M5 DoV: an active-condition invariant whose effect beats its
    benchmark (by method) moves weight_effective as computed by hand.
    inv-inflation-persistence-tips: weight_initial=0.85, floor=0.40
    (dalio tier); 4 confirmations, 1 infirmation. The weight takes no date:
    an invariant is timeless, so whether its condition holds today cannot move
    it (owner, 2026-10-03)."""
    weight_initial, floor_weight = 0.85, 0.40
    confirmations, infirmations = 4, 1
    score, w_eff = invariants.compute_weight_update(
        weight_initial, floor_weight, confirmations, infirmations
    )
    # By hand: score=4/5=0.8; weight=max((0.85*4 + 4) / (4 + 5), 0.40)=7.4/9.
    assert score == pytest.approx(0.8)
    assert w_eff == pytest.approx(7.4 / 9)


# -- the verdict, judged at fixed checkpoints --------------------------------

_CHECKPOINTS = invariants.checkpoints(10, 320)
_THETA, _LEVEL = 0.60, 0.05


def _verdict(confirmations: int, checkpoint: int, null_rate: float = 0.50) -> str:
    """The verdict on `confirmations` among the first `checkpoint` decided
    moments of a record, with the seeded thresholds."""
    bars = invariants.checkpoint_bars(_CHECKPOINTS, _LEVEL, null_rate, _THETA)
    rejection = invariants.rejection_bars(_CHECKPOINTS, _LEVEL, _THETA)
    return invariants.checkpoint_verdict(
        confirmations, checkpoint, bars.get(checkpoint), rejection.get(checkpoint)
    )


def _share_ever_rejected(true_rate: float, lives: int = 100_000) -> float:
    """Simulated, like `_share_ever_integrated`: independent records confirming
    at `true_rate`, each read at every checkpoint against the rejection bars."""
    rejection = invariants.rejection_bars(_CHECKPOINTS, _LEVEL, _THETA)
    outcomes = np.random.default_rng(20261006).random((lives, _CHECKPOINTS[-1])) < true_rate
    confirmed_at = np.cumsum(outcomes, axis=1)[:, np.array(_CHECKPOINTS) - 1]
    allowed = np.array([-1 if rejection[c] is None else rejection[c] for c in _CHECKPOINTS])
    return float((confirmed_at <= allowed).any(axis=1).mean())


def _share_ever_integrated(true_rate: float, null_rate: float, lives: int = 100_000) -> float:
    """Simulated, and deliberately NOT by the calculation that sets the bars:
    `lives` independent records confirming at `true_rate`, each read at every
    checkpoint against the bars for `null_rate`."""
    bars = invariants.checkpoint_bars(_CHECKPOINTS, _LEVEL, null_rate, _THETA)
    outcomes = np.random.default_rng(20261005).random((lives, _CHECKPOINTS[-1])) < true_rate
    confirmed_at = np.cumsum(outcomes, axis=1)[:, np.array(_CHECKPOINTS) - 1]
    required = np.array([np.inf if bars[c] is None else bars[c] for c in _CHECKPOINTS])
    return float((confirmed_at >= required).any(axis=1).mean())


@pytest.mark.parametrize("null_rate", [0.50, 0.41, 0.68])
def test_a_claim_that_knows_nothing_is_rarely_integrated_over_a_whole_life(
    null_rate: float,
) -> None:
    """THE GUARANTEE (owner decision D5): 5% is the probability over the LIFE
    of a record, not at each look. The rule this replaces held 5% at every
    confrontation, and a fair coin was integrated at least once in 20% of
    160-moment lives. Held against the null of each protocol, because 0.50 is
    not the null of all of them."""
    share = _share_ever_integrated(true_rate=null_rate, null_rate=null_rate)
    assert share <= _LEVEL + 0.002  # three standard errors of a 5% share over 100,000 lives


def test_a_fair_coin_null_would_not_hold_the_level_on_a_skewed_protocol() -> None:
    """Why the null is measured per protocol: on a protocol where nothing
    confirms 68% of the time, bars computed for a fair coin integrate what
    knows nothing far more often than the level allows."""
    assert _share_ever_integrated(true_rate=0.68, null_rate=0.50, lives=20_000) > 0.50


def test_a_claim_with_a_real_effect_is_still_integrated() -> None:
    """The bars are reachable: a claim confirming 70% of the time is integrated
    within 100 decided moments nineteen times in twenty."""
    bars = invariants.checkpoint_bars(_CHECKPOINTS, _LEVEL, 0.50, _THETA)
    outcomes = np.random.default_rng(7).random((20_000, 100)) < 0.70
    reached = [c for c in _CHECKPOINTS if c <= 100]
    confirmed_at = np.cumsum(outcomes, axis=1)[:, np.array(reached) - 1]
    required = np.array([bars[c] for c in reached])
    assert (confirmed_at >= required).any(axis=1).mean() > 0.93


def test_the_bars_of_a_fair_coin_null() -> None:
    """Pinned by hand at the first checkpoint: ten of ten is 0.5^10, about one
    in a thousand, and nine of ten or better is eleven in 1,024 — more than the
    whole level the first checkpoint may spend (5% / 32). Past 80 moments the
    bar is theta itself."""
    bars = invariants.checkpoint_bars(_CHECKPOINTS, _LEVEL, 0.50, _THETA)
    assert [bars[c] for c in (10, 20, 30, 40, 50, 80, 100, 160, 320)] == [
        10,
        17,
        23,
        29,
        35,
        52,
        63,
        96,
        192,
    ]


def test_no_bar_is_below_theta_whatever_the_null() -> None:
    """Effect size AND evidence: a null of 0.30 makes six of ten rare, and six
    of ten is still not an effect worth acting on at a hundred moments."""
    for null_rate in (0.30, 0.50, 0.68):
        bars = invariants.checkpoint_bars(_CHECKPOINTS, _LEVEL, null_rate, _THETA)
        assert all(bar is None or bar >= _THETA * count - 1e-9 for count, bar in bars.items())
    skewed = invariants.checkpoint_bars(_CHECKPOINTS, _LEVEL, 0.68, _THETA)
    fair = invariants.checkpoint_bars(_CHECKPOINTS, _LEVEL, 0.50, _THETA)
    assert skewed[100] > fair[100]


def test_no_record_is_judged_before_its_first_checkpoint() -> None:
    """Nine of nine and none of nine are both 'proposed': a record that has not
    reached a checkpoint has not been looked at."""
    assert invariants.checkpoint_reached(9, 10, 320) == 0
    assert _verdict(9, 0) == "proposed"
    assert _verdict(0, 0) == "proposed"


def test_a_record_is_read_at_the_last_checkpoint_it_has_passed() -> None:
    assert invariants.checkpoint_reached(10, 10, 320) == 10
    assert invariants.checkpoint_reached(39, 10, 320) == 30
    assert invariants.checkpoint_reached(500, 10, 320) == 320  # the last one stands


def test_integration_needs_the_bar_of_the_checkpoint() -> None:
    assert _verdict(10, 10) == "integrated"
    assert _verdict(9, 10) == "proposed"  # 0.90, and a coin does it 1.1% of the time
    assert _verdict(17, 20) == "integrated"
    assert _verdict(16, 20) == "proposed"
    assert _verdict(52, 80) == "integrated"  # the real gold invariant's first 80 moments
    assert _verdict(51, 80) == "proposed"


def test_a_claim_worth_integrating_is_rarely_rejected_over_a_whole_life() -> None:
    """THE SAME GUARANTEE ON THE OTHER SIDE (owner, 2026-10-06): a claim
    confirming at exactly theta — the weakest one worth integrating — is
    rejected at least once over its life at most 5% of the time. The branches
    this replaces tested at every checkpoint and rejected it in 21.6% of
    lives; a better claim is rejected more rarely still."""
    assert _share_ever_rejected(true_rate=_THETA) <= _LEVEL + 0.002
    assert _share_ever_rejected(true_rate=0.65, lives=20_000) < 0.01


def test_nothing_stays_proposed_forever() -> None:
    """ADR-006, and what the rejection bar must not cost: a claim that knows
    nothing still leaves 'proposed' — two fair coins in three within 160
    decided moments, nearly all within 320 — and one worse than nothing leaves
    fast."""
    fair = np.random.default_rng(11).random((20_000, 320)) < 0.50
    rejection = invariants.rejection_bars(_CHECKPOINTS, _LEVEL, _THETA)
    confirmed_at = np.cumsum(fair, axis=1)[:, np.array(_CHECKPOINTS) - 1]
    allowed = np.array([-1 if rejection[c] is None else rejection[c] for c in _CHECKPOINTS])
    rejected_by = (confirmed_at <= allowed).cumsum(axis=1) > 0
    assert rejected_by[:, _CHECKPOINTS.index(160)].mean() > 0.60
    assert rejected_by[:, -1].mean() > 0.93
    assert _verdict(96, 320) == "rejected"  # 0.30 at the last checkpoint
    assert _verdict(160, 320) == "rejected"  # 0.50


def test_the_rejection_bars() -> None:
    """Pinned by hand at the first checkpoint: at theta, no confirmation in ten
    is 0.4^10, one in ten thousand; one or none is seventeen in ten thousand —
    more than the first checkpoint may spend (5% / 32)."""
    rejection = invariants.rejection_bars(_CHECKPOINTS, _LEVEL, _THETA)
    assert [rejection[c] for c in (10, 20, 30, 50, 100, 160, 320)] == [0, 5, 10, 20, 47, 81, 173]
    assert _verdict(0, 10) == "rejected"
    assert _verdict(3, 10) == "proposed"  # 0.30: 'refuted' under the old branch, 5.5% at theta
    assert _verdict(47, 100) == "rejected"
    assert _verdict(48, 100) == "proposed"


def test_the_two_bars_cannot_meet() -> None:
    """One is at or above theta of its count, the other below it: no record is
    both integrated and rejected, whatever the null."""
    rejection = invariants.rejection_bars(_CHECKPOINTS, _LEVEL, _THETA)
    for null_rate in (0.30, 0.50, 0.68):
        bars = invariants.checkpoint_bars(_CHECKPOINTS, _LEVEL, null_rate, _THETA)
        for count in _CHECKPOINTS:
            bar, lower = bars[count], rejection[count]
            assert bar is None or lower is None or lower < bar


def test_verdict_dead_middle_stays_proposed_while_genuinely_unresolved() -> None:
    """A mid-band score on a short record keeps 'proposed' — theta is still a
    plausible source of the evidence, so it is genuinely insufficient (the
    ONLY meaning of 'proposed')."""
    assert _verdict(30, 60) == "proposed"  # 0.500 on 60 moments
    assert _verdict(5, 10) == "proposed"


def test_verdict_evidence_eventually_settles_every_true_rate() -> None:
    """'Nothing stays proposed forever' (ADR-006), against BOTH bars: a true
    rate either side of theta resolves once enough moments accrue, and only
    the rate exactly AT theta is allowed to stall."""
    for true_rate, expected in ((0.70, "integrated"), (0.50, "rejected"), (0.30, "rejected")):
        assert _verdict(round(true_rate * 320), 320) == expected


def test_the_null_of_a_protocol_is_measured_not_assumed() -> None:
    """A median baseline puts half the dates on each side; the margin then
    removes its share from each, and not an equal share when the distribution
    leans. Here the median is 0.0, two dates fall well below it and one well
    above: a condition that knows nothing confirms one time in three."""
    excess = [-0.10, -0.10, 0.0, 0.01, 0.20]
    assert invariants.null_confirmation_rate(excess, 0.0, "outperform", 0.02) == pytest.approx(
        1 / 3
    )
    assert invariants.null_confirmation_rate(excess, 0.0, "underperform", 0.02) == pytest.approx(
        2 / 3
    )
    # Nothing leaves the margin: there is no rate to report, and saying 0.50
    # would be asserting one.
    assert invariants.null_confirmation_rate([0.0, 0.01, -0.01], 0.0, "outperform", 0.02) is None


# -- condition / moment evaluation ------------------------------------------


def test_confront_moment_outperform_and_underperform() -> None:
    assert invariants.confront_moment(0.10, 0.02, "outperform", 0.05) == "confirmed"
    assert invariants.confront_moment(-0.05, 0.02, "outperform", 0.05) == "refuted"
    assert invariants.confront_moment(0.015, 0.02, "outperform", 0.05) == "neutral"  # in margin
    assert invariants.confront_moment(-0.20, -0.05, "underperform", 0.05) == "confirmed"
    assert invariants.confront_moment(0.10, -0.05, "underperform", 0.05) == "refuted"


def test_a_moment_that_could_not_be_measured_is_not_a_neutral_one() -> None:
    """The two were one `None`. 'neutral' says the claim was tested and the
    effect stayed inside the margin; 'no_data' says it was not tested. Only
    telling them apart lets coverage be reported."""
    assert invariants.confront_moment(None, 0.02, "outperform", 0.05) == "no_data"
    assert invariants.confront_moment(0.02, None, "outperform", 0.05) == "no_data"
    assert invariants.confront_moment(0.02, 0.02, "outperform", 0.05) == "neutral"


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
    assert all(v == "neutral" for v in relative)  # no skill shown => no verdict


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


def test_validate_invariant_demotes_a_non_object_effect_instead_of_raising() -> None:
    """Measured on the on-stack M8b run, 2008-09-02: a Worker wrote `effect` as
    prose. `spec` is `dict[str, Any]`, so Pydantic validates the envelope and
    nothing under it — and this function, which returns a REASON for every other
    malformed shape, reached `.get` on a string and raised AttributeError out
    through Writeback and the whole decision cycle.

    A demotion reason is the contract ("a malformed condition/effect never
    silently breaks maturation"); the raise was the one field that escaped it."""
    reason = invariants.validate_invariant(
        [],
        "gold outperforms when real yields are negative",  # type: ignore[arg-type]  # the shape under test
        _seed_registries(),
    )
    assert reason is not None and "expected an object" in reason


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


def test_an_absolute_claim_on_cash_is_not_measurable_and_a_relative_one_is() -> None:
    """ "Cash loses purchasing power when inflation runs hot" is a claim about
    REAL return. Encoded on the nominal `return`, every moment is neutral and
    the claim keeps its starting weight for good, so the gate sends it to
    reference knowledge. Cash against the other classes is a different claim,
    and a measurable one."""
    condition = [{"signal": "inflation", "feature": "level", "op": ">", "value": 3.0}]
    effect = {
        "handle": "asset-class:cash",
        "metric": "return",
        "method": "absolute",
        "direction": "underperform",
    }
    reason = invariants.validate_invariant(condition, effect, _seed_registries())
    assert reason is not None and "real-return" in reason
    relative = {**effect, "method": "cross_class"}
    assert invariants.validate_invariant(condition, relative, _seed_registries()) is None


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


# -- what a record says beyond its weight -------------------------------------


def test_lift_is_signed_in_favour_of_the_claim_whichever_way_it_points() -> None:
    assert invariants.moment_lift(0.08, 0.03, "outperform") == pytest.approx(0.05)
    assert invariants.moment_lift(0.08, 0.03, "underperform") == pytest.approx(-0.05)
    assert invariants.moment_lift(None, 0.03, "outperform") is None


def test_the_range_says_what_the_rate_alone_hides() -> None:
    """4 of 5 and 40 of 50 are the same rate and not the same knowledge."""
    few = invariants.wilson_interval(4, 5)
    many = invariants.wilson_interval(40, 50)
    assert few is not None and many is not None
    assert few == pytest.approx((0.376, 0.964), abs=1e-3)
    assert many == pytest.approx((0.670, 0.888), abs=1e-3)
    assert invariants.wilson_interval(0, 0) is None


def _moment(day: str, verdict: str, lift: float | None) -> dict[str, object]:
    return {"signal_date": day, "verdict": verdict, "lift": lift}


def test_a_summary_reports_size_tail_stability_and_coverage() -> None:
    """Often right and badly wrong when wrong: the hit rate is 0.75 and the mean
    lift is negative. The verdict reads the first; the summary shows both, and
    that the record was earned in its early half."""
    moments = [
        _moment("2001-01-01", "confirmed", 0.03),
        _moment("2002-01-01", "confirmed", 0.03),
        _moment("2003-01-01", "confirmed", 0.03),
        _moment("2004-01-01", "neutral", 0.00),
        _moment("2005-01-01", "confirmed", 0.03),
        _moment("2006-01-01", "refuted", -0.40),
        _moment("2007-01-01", "confirmed", 0.03),
        _moment("2008-01-01", "refuted", -0.40),
        _moment("1991-01-01", "no_data", None),
    ]
    summary = invariants.summarize_evidence("return", moments)

    assert (summary.confirmed, summary.refuted, summary.neutral, summary.no_data) == (5, 2, 1, 1)
    assert summary.rate == pytest.approx(5 / 7)
    assert summary.mean_lift == pytest.approx(-0.65 / 8)
    assert summary.worst_lift == pytest.approx(-0.40)
    assert summary.rate_early == pytest.approx(1.0)  # the first 3 decided
    assert summary.rate_late == pytest.approx(0.5)  # the last 4

    line = invariants.describe_evidence(summary)
    assert line.startswith("5/7 confirmed (rate 0.71, 95% range ")
    assert "1 neutral; 1 unmeasurable" in line
    assert "mean lift -0.081 on return, worst -0.400" in line
    assert "early half 1.00, late half 0.50" in line


def test_a_record_with_nothing_decided_says_so() -> None:
    summary = invariants.summarize_evidence("return", [_moment("2001-01-01", "neutral", 0.001)])
    assert summary.rate is None and summary.rate_range is None
    assert invariants.describe_evidence(summary).startswith("no decided moment yet; 1 neutral")
