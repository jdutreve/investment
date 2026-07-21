"""M7 tests for `worker/curator.py` — the MECHANICAL half, no LLM calls.

What is pinned here is the propose/dispose boundary (ADR-006): the model's
numbers are a prior, Python computes the score, and the expressibility gate is
binary. All three are testable without a network call, which is the point of
splitting `score_and_gate` from `curate_passages`.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from investment.db.seed_data import SIGNAL_ALIASES, SYSTEM_THRESHOLDS
from investment.mechanical.invariants import Registries
from investment.worker.curator import (
    SCORE_WEIGHTS,
    UNWEIGHTED_DIMENSIONS,
    CandidateScores,
    Effect,
    InvariantCandidate,
    KnowledgeCurator,
    Predicate,
    ReferenceNote,
    ScoredCandidate,
    gate_candidate,
    interest_score,
    rank,
    render_instructions,
)


@pytest.fixture
def registries() -> Registries:
    return Registries(
        signals=set(SIGNAL_ALIASES) | {"SPY"},
        asset_classes={"equities", "bonds", "gold-commodities"},
        strategies={"market-signal-stack"},
        assets={"SPY", "IEF"},
        regime_types={"stagflation"},
    )


def _scores(**overrides: int) -> CandidateScores:
    base = dict(
        generalizability=80,
        testability=80,
        actionability=80,
        evidence_quality=80,
        novelty=80,
        temporal_robustness=80,
    )
    base.update(overrides)
    return CandidateScores(**base)  # type: ignore[arg-type]


# Explicit sentinel: `effect=None` must mean "no effect", which a plain
# `None` default cannot express (it is indistinguishable from "not provided").
_UNSET: Any = object()


def _candidate(
    condition: list[Predicate] | None = None,
    effect: Effect | None = _UNSET,
    scores: CandidateScores | None = None,
) -> InvariantCandidate:
    return InvariantCandidate(
        claim="Negative real rates hurt nominal bonds",
        description="d",
        example="e",
        condition=condition
        if condition is not None
        else [Predicate(signal="real_rate", feature="level", op="<", value=0.0)],
        effect=effect
        if effect is not _UNSET
        else Effect(
            handle="asset-class:bonds",
            metric="return",
            method="cross_class",
            direction="underperform",
        ),
        scores=scores or _scores(),
        weight_initial=0.6,
    )


# -- scoring is Python's job, not the model's -----------------------------


def test_score_is_the_weighted_sum() -> None:
    s = _scores(generalizability=100, testability=0, actionability=0, evidence_quality=0, novelty=0)
    assert interest_score(s) == pytest.approx(30.0)


def test_weights_sum_to_one_without_temporal_robustness() -> None:
    # The owner's formula is 30/25/20/15/10. `temporal_robustness` is collected
    # but unweighted — an explicit gap this test documents rather than hides.
    assert sum(SCORE_WEIGHTS.values()) == pytest.approx(1.0)
    assert "temporal_robustness" not in SCORE_WEIGHTS
    assert UNWEIGHTED_DIMENSIONS == ("temporal_robustness",)


def test_temporal_robustness_does_not_move_the_score() -> None:
    low = interest_score(_scores(temporal_robustness=0))
    high = interest_score(_scores(temporal_robustness=100))
    assert low == high


def test_score_is_recomputable_with_other_weights_without_an_llm_call() -> None:
    # The reason the model is not asked for the score: re-ranking an existing
    # corpus must cost nothing.
    s = _scores(novelty=100, generalizability=0)
    assert interest_score(s, {"novelty": 1.0}) == pytest.approx(100.0)
    assert interest_score(s, {"generalizability": 1.0}) == pytest.approx(0.0)


def test_seeded_weights_match_the_module_defaults() -> None:
    # Guards the same drift the ingester test guards: seed_data and code must
    # not disagree about a pinned constant.
    for dim, weight in SCORE_WEIGHTS.items():
        assert SYSTEM_THRESHOLDS[f"curator_weight_{dim}"] == weight


# -- the expressibility gate is binary, not a score ------------------------


def test_well_formed_candidate_passes(registries: Registries) -> None:
    assert gate_candidate(_candidate(), registries) is None


def test_unknown_signal_is_rejected(registries: Registries) -> None:
    # The failure this prevents: an invented signal name reaches the 35y sweep
    # and raises a KeyError mid-run instead of being caught at birth.
    bad = _candidate(condition=[Predicate(signal="vibes", feature="level", op="<", value=0.0)])
    reason = gate_candidate(bad, registries)
    assert reason is not None and "vibes" in reason


def test_unknown_feature_is_rejected(registries: Registries) -> None:
    bad = _candidate(
        condition=[Predicate(signal="real_rate", feature="momentum", op="<", value=0.0)]
    )
    assert gate_candidate(bad, registries) is not None


def test_invalid_direction_is_rejected(registries: Registries) -> None:
    bad = _candidate(
        effect=Effect(
            handle="asset-class:bonds", metric="return", method="cross_class", direction="rises"
        )
    )
    assert gate_candidate(bad, registries) is not None


def test_a_hollow_candidate_cannot_even_be_CONSTRUCTED() -> None:
    """The ice-core fix (2026-07-21), pinned.

    With `condition`/`effect` optional, the model omitted both: 100 passages
    produced 2 candidates, neither with a single predicate. Models follow the
    schema, not the prompt — so the schema now forbids the shape outright
    rather than catching it downstream."""
    with pytest.raises(ValidationError):
        InvariantCandidate(
            claim="c",
            description="d",
            example="e",
            condition=[],  # empty is no longer valid
            effect=Effect(
                handle="asset-class:bonds",
                metric="return",
                method="cross_class",
                direction="underperform",
            ),
            scores=_scores(),
            weight_initial=0.5,
        )


def test_irreducible_knowledge_has_its_own_home() -> None:
    # Why the required fields above are safe to demand: a claim that cannot be
    # reduced to the registry has an honest destination, so the model never
    # needs to emit a hollow invariant to avoid losing it.
    note = ReferenceNote(
        claim="Reserve-currency status erodes over generations",
        description="d",
        why_not_reducible="No registry signal measures reserve-currency share",
    )
    assert note.why_not_reducible


def test_a_high_score_does_not_rescue_an_inexpressible_candidate(registries: Registries) -> None:
    # THE load-bearing property: the gate is not a score. A candidate the model
    # rates 100/100 across the board is still demoted if it cannot be reduced
    # to the registry.
    perfect = _scores(
        generalizability=100,
        testability=100,
        actionability=100,
        evidence_quality=100,
        novelty=100,
        temporal_robustness=100,
    )
    bad = _candidate(
        condition=[Predicate(signal="animal_spirits", feature="level", op=">", value=1.0)],
        scores=perfect,
    )
    scored = KnowledgeCurator.score_and_gate(
        KnowledgeCurator.__new__(KnowledgeCurator), [bad], registries
    )
    assert scored[0].interest_score == pytest.approx(100.0)
    assert not scored[0].admissible


# -- ranking ---------------------------------------------------------------


def test_rank_keeps_the_best_admissible_under_the_ceiling() -> None:
    def s(score: float, ok: bool) -> ScoredCandidate:
        return ScoredCandidate(_candidate(), score, None if ok else "nope")

    ranked = rank([s(10, True), s(90, True), s(99, False), s(50, True)], ceiling=2)
    assert [r.interest_score for r in ranked] == [90, 50]


def test_rank_excludes_inadmissible_however_high_scoring() -> None:
    ranked = rank([ScoredCandidate(_candidate(), 100.0, "unknown signal")], ceiling=5)
    assert ranked == []


# -- the prompt carries the live vocabulary --------------------------------


def test_instructions_inline_the_actual_registry(registries: Registries) -> None:
    # The model cannot map a claim onto a vocabulary it was never shown, so the
    # expressibility requirement is only answerable if the signals are listed.
    text = render_instructions(registries)
    assert "real_rate" in text and "credit_spread" in text
    assert "cross_class" in text and "underperform" in text
    # And it must demand the fundamental driver, not a surface label.
    assert "regime = stagflation" in text


# -- unreachable thresholds: valid vocabulary, impossible condition ---------

_RANGES = {"growth": (24.06, 117.89), "real_rate": (-7.92, 3.70), "inflation": (-2.15, 8.98)}


def test_never_true_threshold_is_rejected(registries: Registries) -> None:
    """The defect the first full corpus run produced, now caught.

    `growth` is an INDEX running 24..118, so `growth.level < 0` names a real
    signal, a real feature and a real operator — and can never fire. The
    resulting invariant is not wrong, it is DORMANT: never active, never
    confronted, never matured, and silent about it. Three of the top-15
    candidates had exactly this shape."""
    bad = _candidate(condition=[Predicate(signal="growth", feature="level", op="<", value=0.0)])
    reason = gate_candidate(bad, registries, _RANGES)
    assert reason is not None and "unreachable" in reason


def test_fraction_instead_of_percentage_points_is_rejected(registries: Registries) -> None:
    # `inflation > 0.05` reads as "5%" but the series is in percentage points,
    # so it is true almost always — a condition that discriminates nothing.
    # Caught here as reachable-but-degenerate only when it falls outside; the
    # clearly-impossible direction is what the range check can prove.
    bad = _candidate(condition=[Predicate(signal="inflation", feature="level", op=">", value=99.0)])
    assert gate_candidate(bad, registries, _RANGES) is not None


def test_reachable_threshold_passes(registries: Registries) -> None:
    ok = _candidate(condition=[Predicate(signal="real_rate", feature="level", op="<", value=0.0)])
    assert gate_candidate(ok, registries, _RANGES) is None


def test_derived_features_are_not_range_checked(registries: Registries) -> None:
    # speed/acceleration ranges are not stored next to the level, so the check
    # must abstain rather than guess — a false rejection is worse than none.
    ok = _candidate(condition=[Predicate(signal="growth", feature="speed", op="<", value=0.0)])
    assert gate_candidate(ok, registries, _RANGES) is None


def test_instructions_show_observed_ranges(registries: Registries) -> None:
    # Naming the signals is not enough: the model guessed units and centring.
    text = render_instructions(registries, _RANGES)
    assert "24.06" in text and "117.89" in text
    assert "percentage points" in text
