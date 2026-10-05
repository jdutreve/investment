"""Invariant confrontation + birth maturation + contradiction check
(docs/TASKS.md Phase 5bis `invariants.py`; docs/ARCHITECTURE.md "Invariant
confrontation rule" / "Birth maturation" / "Invariant contradiction check";
docs/DATA_MODELS.md Invariant entity; CLAUDE.md "Invariant weight model").

M5 scope is the FROM-BACKTESTS branch of the confrontation rule
(`mature_invariant()`, run once per invariant at birth — seed invariants are
just the first batch, docs/USE_CASES.md UC0 step 11b) plus the shared
`compute_weight_update()` primitive every future confrontation source
(evaluation/proposal, M8) funnels into, and the contradiction check (run at
seed after 11b/11c, and read on the current integrated set by
`alerts.invariant_contradiction_alert` whenever the alerts are collected).

A confrontation is BASELINE-RELATIVE: a confirmation means the effect beat
what the handle delivers with the condition IGNORED, not merely that the
effect occurred (`baseline_excess`). Measured absolutely, any invariant whose
effect points along a strong base rate self-certifies — equities beat the
median of the other classes ~70% of any 12w window on the risk premium alone,
so "rising growth favours equities" scored 0.65 and integrated while
performing WORSE than ignoring growth entirely. `market_score` keeps its
pinned formula (CLAUDE.md); only what counts as a confirmation changed, which
is what re-anchors its null to 0.50 for every handle.

Split the same way as the other mechanical modules: a PURE core (predicate
evaluation, moment/episode detection, the weight formula, contradiction
disjointness) and a thin async DB layer that reads `market_data` /
`benchmark_valuation` / `regime` and writes `invariant_confrontations` +
`invariant`.
"""

import dataclasses
import hashlib
import json
import logging
import math
import operator
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd
from ulid import ULID

from investment.db.seed_data import BENCHMARK_CLASSES, SIGNAL_ALIASES
from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios
from investment.mechanical.backtests import (
    BENCHMARK_KIND_ASSET,
    BENCHMARK_KIND_ASSET_CLASS,
    BENCHMARK_KIND_STRATEGY,
    BENCHMARK_METRICS,
    investable_tickers,
)

logger = logging.getLogger(__name__)

_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}
# `feature` is valid FOR ITS SIGNAL, not globally (docs/ARCHITECTURE.md
# VALIDATION GATE: "`feature` valid for it"): a market series carries the
# level/speed/acceleration columns `market/derivatives.py` computes, while
# 'regime' is a step function of RegimeType ids and carries only 'type'.
# Mixing them is a KeyError mid-sweep, not a demotion.
_SERIES_FEATURES = {"level", "speed", "acceleration"}
_REGIME_SIGNAL = "regime"
_REGIME_FEATURES = {"type"}
_VALID_METHODS = {"cross_class", "cross_strategy", "absolute"}
_VALID_DIRECTIONS = {"outperform", "underperform"}
_CASH_HANDLE = "asset-class:cash"

# The FOURTH invariant status (docs/DATA_MODELS.md reference knowledge: "an
# invariant with empty condition/no effect IS reference knowledge: never
# confronted, market_score stays 1.0"). It is a TERMINAL state, not a stage:
# there is no condition to confront, so no measurement can ever move it, and
# ADR-006's "nothing stays proposed forever" simply does not apply to it. That
# is exactly why it needed a name of its own — filed as 'proposed' it was an
# open promise the system could never keep.
#
# A STATUS rather than a separate entity, deliberately: DATA_MODELS is explicit
# that "a ponctual fact is NOT a new entity", and reference knowledge still
# informs Worker reasoning through the same corpus, the same embedding and the
# same semantic search as any other invariant. Only its verdict lifecycle
# differs.
REFERENCE_STATUS = "reference"

# -- pure core: weight formula (CLAUDE.md "Invariant weight model") --------


def market_score(confirmations: int, infirmations: int) -> float:
    """`confirmations / (confirmations + infirmations)`, 1.0 until the first
    confrontation."""
    total = confirmations + infirmations
    return confirmations / total if total > 0 else 1.0


# How many confrontations an author's reputation is worth (owner, 2026-10-03).
# `weight_initial` enters the weight as this many observations at that rate, so
# 4 real confrontations weigh as much as the prior and every one after that
# outweighs it. 4 because it is the count at which the verdict first allows
# itself to reject (`invariant_refuted_min_confrontations`): the prior stops
# dominating exactly when the evidence is first trusted to overrule it.
PRIOR_CONFRONTATIONS = 4.0


def weight_effective(
    weight_initial: float, confirmations: int, infirmations: int, floor_weight: float
) -> float:
    """BELIEF IS A PRIOR THE EVIDENCE REPLACES (owner, 2026-10-03):
    `(weight_initial x k + confirmations) / (k + N)`, k = `PRIOR_CONFRONTATIONS`.
    With no confrontation it is `weight_initial`; as N grows it converges on the
    measured rate, from above or from below.

    It used to be `weight_initial x market_score`, which made the starting
    weight a CEILING: the score is a proportion, so measurement could only ever
    take weight away. An unmeasured claim (score 1.0 by default) outweighed the
    same claim confirmed 65% of the time, a system-tier invariant could never
    exceed 0.25 whatever its record, and reference notes averaged 0.656 against
    0.562 for the integrated invariants. "Notoriety sets where a claim starts,
    not where it stops" (CLAUDE.md) was true downward only.

    AN INVARIANT IS TIMELESS (owner, same day): no date enters here. Whether the
    condition holds TODAY is applicability (`active_invariant_ids`), a separate
    question the weight must not answer. A `recency_factor` used to multiply in,
    counting days since the condition last held — the spec said a dormant
    invariant "must NOT decay" and pinned a formula that decayed it."""
    total = confirmations + infirmations
    posterior = (weight_initial * PRIOR_CONFRONTATIONS + confirmations) / (
        PRIOR_CONFRONTATIONS + total
    )
    return max(posterior, floor_weight)


def compute_weight_update(
    weight_initial: float,
    floor_weight: float,
    confirmations: int,
    infirmations: int,
) -> tuple[float, float]:
    """`(market_score, weight_effective)` — the single computation every
    confrontation source (backtest/evaluation/proposal) funnels into
    (docs/ARCHITECTURE.md 'Invariant confrontation rule':
    "update_invariant_weights()")."""
    return (
        market_score(confirmations, infirmations),
        weight_effective(weight_initial, confirmations, infirmations, floor_weight),
    )


def _binomial_pmf(successes: int, total: int, rate: float) -> float:
    return math.comb(total, successes) * rate**successes * (1.0 - rate) ** (total - successes)


def binomial_tail_at_least(successes: int, total: int, rate: float) -> float:
    """`P(X >= successes)` for `X ~ Binomial(total, rate)` — "could a process
    with this rate have produced evidence THIS good, by luck?". Feeds the
    INTEGRATED branch against `rate` = the null."""
    if total == 0:
        return 1.0
    return sum(_binomial_pmf(k, total, rate) for k in range(successes, total + 1))


def binomial_tail_at_most(successes: int, total: int, rate: float) -> float:
    """`P(X <= successes)` for `X ~ Binomial(total, rate)` — "could a process
    with this rate have produced evidence THIS bad, by luck?". Feeds the
    INADEQUATE branch against `rate` = theta."""
    if total == 0:
        return 1.0
    return sum(_binomial_pmf(k, total, rate) for k in range(successes + 1))


def time_validation_verdict(
    confirmations: int,
    infirmations: int,
    score: float,
    n_min: float,
    theta: float,
    refuted_min_confrontations: float,
    refuted_score: float,
    verdict_confidence: float,
    null_score: float,
) -> str:
    """'integrated' | 'rejected' | 'proposed' (docs/ARCHITECTURE.md "Birth
    maturation" TIME-VALIDATION VERDICT; ADR-006 + its M5/M5-bis amendments —
    mechanical, no user gate). Three outcomes, checked in order:

    - REFUTED (rejected): the effect actively fails when the condition holds
      (point test — arms fast at small N for clearly harmful invariants).
    - INTEGRATED: the effect is BIG ENOUGH (point estimate clears theta) AND
      DEMONSTRATED (the null — `null_score`, the no-condition rate of a
      baseline-relative score — would produce evidence this good less than
      `1 - verdict_confidence` of the time). Both, because they answer
      different questions: theta is "is this worth acting on?", the tail
      test is "do we know it at all, or did a coin land well?".
    - INADEQUATE (rejected): a true rate of theta would produce evidence
      this BAD less than `1 - verdict_confidence` of the time — given ample
      evidence, the invariant demonstrably cannot reach the bar. This is the
      branch that empties the 0.35..theta dead middle, where 4 of 6 seed
      invariants would otherwise sit 'proposed' forever at any N (e.g. 0.545
      on N=354) — violating "Nothing stays proposed forever" (ADR-006). It
      cannot race INTEGRATED: score >= theta puts the observation at or above
      theta's own median, so its lower tail is ~0.5, never <= alpha.

    The tail test is what makes theta mean anything. Alone, a point test gets
    EASIER the less evidence there is: at n_min=3 an invariant with no edge
    whatsoever integrated 50% of the time (2 of 3 confirmations is a coin
    flip), and 21% of the time at N=14 — which is how
    `inv-inflation-persistence-tips` held an 'integrated' stamp on 9/14.
    Worse, the incentive ran the wrong way: a narrower condition yields fewer
    moments and so passed MORE easily, exactly rewarding the over-fitted
    invariants the engine exists to catch — and under ADR-006 nothing
    downstream would have caught it. The bar stays reachable: a true 0.65
    invariant qualifies on ~30 moments (~7y of active condition at a 12w
    horizon), and the real gold invariant clears it at 53/82 (tail 0.005).

    EXACT tails, not the normal-approximation interval this rule first used:
    a Wilson bound is liberal at extreme rates with small N, precisely where
    the defect lives. `wilson_lower(3, 3) = 0.526` would have integrated a
    3-for-3 invariant that a coin reproduces 12.5% of the time. The exact
    tail puts the minimum perfect record at 5/5 (0.031) and leaves every
    rejection on the current board unchanged.

    'proposed' means exactly one thing: INSUFFICIENT EVIDENCE — and it still
    empties mechanically as confrontations accrue: at a true rate above theta
    the null's tail collapses (integrating), below theta the theta-tail
    collapses (rejecting). The verdict is STATELESS (recomputed from current
    counts at every confrontation), so a rejection is as reversible as the
    evidence that produced it.

    Refutation is checked first: a refuted invariant is never 'integrated'
    even if it also happens to clear the bars on a stale read (it cannot, by
    construction — theta > refuted_score — but the order documents the
    precedence)."""
    total = confirmations + infirmations
    alpha = 1.0 - verdict_confidence
    if total >= refuted_min_confrontations and score < refuted_score:
        return "rejected"
    if (
        total >= n_min
        and score >= theta
        and binomial_tail_at_least(confirmations, total, null_score) <= alpha
    ):
        return "integrated"
    if (
        total >= refuted_min_confrontations
        and binomial_tail_at_most(confirmations, total, theta) <= alpha
    ):
        return "rejected"
    return "proposed"


# -- pure core: condition / moment evaluation -------------------------------


def evaluate_condition(
    condition: list[dict[str, Any]],
    signal_frames: dict[str, pd.DataFrame],
    regime_type_series: pd.Series,
) -> pd.Series:
    """The boolean daily 'condition ACTIVE' series — predicates ANDed;
    `signal_frames`/`regime_type_series` must already share ONE common,
    forward-filled daily index (point-in-time: a signal's last known reading
    holds until the next print — see `_align_daily`). Empty condition
    ('always') is handled by the caller (`build_moments`), not here."""
    mask: pd.Series | None = None
    for predicate in condition:
        signal, feature = predicate["signal"], predicate["feature"]
        op = _OPS[predicate["op"]]
        value = predicate["value"]
        if signal == "regime":
            m = op(regime_type_series, value)
        else:
            column = signal_frames[signal][feature]
            m = column.notna() & op(column, value)
        mask = m if mask is None else (mask & m)
    return mask if mask is not None else pd.Series(dtype=bool)


def sample_moments(active: pd.Series, horizon: pd.Timedelta) -> list[pd.Timestamp]:
    """Moments across condition-ACTIVE time, spaced at least one `horizon`
    apart: walk the active days forward, take one, skip a horizon, repeat.

    Two properties this buys, both load-bearing:

    NON-OVERLAPPING → the verdict's binomial tails are sound. Each moment's
    outcome
    window is [d, d+horizon], so horizon-spacing makes the windows disjoint
    and the moments quasi-independent — which is exactly what the exact
    binomial tails in `time_validation_verdict` assume (they were a Wilson
    interval when this docstring was written; the assumption is the same one).
    Sampling active time weekly
    instead would overlap every 12w window 12-fold and inflate N (and shrink
    the bound) against evidence that is not there.

    CONTINUOUS IN CONDITION FREQUENCY → no cliff between a persistent state
    and 'always'. One-moment-per-EPISODE had an indefensible discontinuity:
    a condition true 100% of the time sampled ~1800 times, while one true 88%
    of the time in a single block sampled ONCE. Measured on the real data,
    `real_rate < 2.5` holds 88% of 35y but chatters into 36 episodes — one of
    7050 days (2001-2020, the whole low-real-rate era) plus 35 six-day blips
    around the threshold in the high-rate 1990s. Per-episode scoring gave the
    19-year era a single data point and let the blips carry the verdict:
    'low real yields favour gold' read 0.158/REFUTED on N=19, vs 0.542/
    undecided on N=107 here (M5 verification).

    A SHORT episode still contributes its start (the decision moment: "the
    condition just became active — tilt?"), since the next active day within
    a horizon is skipped; a LONG one is sampled throughout instead of
    collapsing to its first day."""
    if active.empty:
        return []
    active_days = active.sort_index()
    days = active_days.index[active_days.fillna(False).to_numpy(dtype=bool)]
    moments: list[pd.Timestamp] = []
    next_eligible: pd.Timestamp | None = None
    for day in days:
        if next_eligible is None or day >= next_eligible:
            moments.append(day)
            next_eligible = day + horizon
    return moments


# What one moment can say. Only the first two are EVIDENCE and count in N; the
# other two are stored so that coverage can be reported — they were one `None`
# until 2026-10-04, and neither was persisted, so "the effect was too small to
# call" and "there was nothing to measure" could not be told apart afterwards.
CONFIRMED, REFUTED = "confirmed", "refuted"
NEUTRAL = "neutral"  # measured, and inside the margin band
NO_DATA = "no_data"  # not measurable: a missing series or an incomplete window
COUNTED_VERDICTS = (CONFIRMED, REFUTED)

# The two MECHANICAL sources of a confrontation: a defined effect measured over
# a completed window. The birth sweep looks back over the whole history; the
# forward sweep confronts, week after week, the moments whose window has
# completed since. Both are re-derivable from the data, which is why a re-sweep
# may replace them.
#
# THEY ARE THE ONLY SOURCES THAT MOVE A STANDING (owner, 2026-10-04: decisions
# rest on measurements only). A Worker evaluation is stored beside them as a
# READING — dated, visible, and counted nowhere: it has no completed window, no
# baseline and no margin, and the reader of a weight must not be one of its
# writers.
BIRTH_SOURCE = "backtest"
FORWARD_SOURCE = "forward"

CONFRONTATION_EVENT = "ConfrontationEvent"
SOURCE_UC = "invariant-forward"


def moment_lift(
    handle_value: float | None, benchmark_value: float | None, direction: str
) -> float | None:
    """HOW MUCH the effect showed at one moment: the handle's value beyond its
    benchmark, signed so that positive is IN FAVOUR of the claim whichever way
    it points. `None` when either side could not be measured.

    A metric is always "higher is better" as stored (return: higher wins;
    max_drawdown: stored as a negative fraction, less negative = higher =
    better) — no metric-specific sign flip needed.

    KEPT, because the verdict throws it away. `confront_moment` compares this
    number to a margin and stores a label, so a hit rate was all an invariant's
    record could say: often right and badly wrong when wrong read the same as
    often right and mildly wrong (docs/IMPROVEMENTS.md "market_score is a pure
    hit rate"). The verdict still reads the label alone; the lift is REPORTED
    beside it."""
    if handle_value is None or benchmark_value is None:
        return None
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(f"unknown direction: {direction!r}")
    diff = handle_value - benchmark_value
    return -diff if direction == "underperform" else diff


def confront_moment(
    handle_value: float | None, benchmark_value: float | None, direction: str, margin: float
) -> str:
    """'confirmed' | 'refuted' | 'neutral' | 'no_data' (docs/ARCHITECTURE.md
    confrontation rule): the moment's lift against the no-op margin."""
    lift = moment_lift(handle_value, benchmark_value, direction)
    if lift is None:
        return NO_DATA
    if lift > margin:
        return CONFIRMED
    if lift < -margin:
        return REFUTED
    return NEUTRAL


def is_absolute_claim(condition: list[dict[str, Any]]) -> bool:
    """Does this invariant claim something UNCONDITIONALLY?

    An empty `condition` is not an absent one in this codebase — it is a claim
    with no predicates, i.e. "this handle outperforms, period". The three places
    that branch on it all mean that, and each says so in its own words:
    `baseline_excess` gives it a 0.0 baseline because there is no conditional
    lift to measure, `condition_descriptor` writes it as "always", and
    `_mature_one` samples it on every date.

    NAMED BECAUSE THE MEANING WAS ONLY EVER IN PROSE. On 2026-08-23 a fix for a
    malformed condition dropped it to `[]` and kept the effect, reasoning that
    `[]` was the neutral, un-measurable state. It is the opposite: the Worker's
    conditional claim silently became the STRONGER unconditional one and would
    have earned a verdict nobody argued for. The docstrings that would have said
    so were three functions away. A predicate cannot be read past — you meet the
    meaning when you write the value."""
    return not condition


def baseline_excess(excess_values: list[float], condition: list[dict[str, Any]]) -> float:
    """The invariant's NO-CONDITION null: the median `excess` the handle
    delivers over ALL dates, condition ignored (docs/ARCHITECTURE.md
    "Invariant confrontation rule" — baseline-relative confrontation).

    A confirmation must mean "the effect happened MORE than it usually does",
    not merely "the effect happened": equities beat the median of the other
    classes ~70% of any 12w window on the risk premium alone, so an absolute
    hit rate certifies that premium, not the condition. Subtracting this
    baseline is what makes `market_score` (unchanged: confirmations /
    (confirmations + infirmations) — CLAUDE.md) a SKILL frequency, whose null
    is 0.50 for every handle. That is the anchor `invariant_time_validation_
    score` (0.60) is written against.

    An EMPTY condition returns a 0.0 baseline: 'always' makes no conditional
    claim, so its lift is zero by construction and lift-scoring would pin it
    at 0.50 forever. Its claim genuinely IS absolute ("this handle's drawdown
    is lower, period"), so an absolute hit rate is the correct measure for it.
    """
    if is_absolute_claim(condition):
        return 0.0
    return float(np.median(excess_values)) if excess_values else 0.0


def condition_descriptor(condition: list[dict[str, Any]]) -> str:
    """`invariant_confrontations.moment_context` for a condition-keyed moment
    (docs/DATA_MODELS.md: "a compact descriptor of the condition that
    held")."""
    if is_absolute_claim(condition):
        return "always"
    return "&".join(f"{p['signal']}.{p['feature']}{p['op']}{p['value']}" for p in condition)


# -- pure core: VALIDATION GATE (docs/ARCHITECTURE.md "Birth maturation") --


@dataclasses.dataclass(frozen=True)
class Registries:
    """Everything the VALIDATION GATE checks a candidate against. One value
    object rather than five loose set arguments — the gate's clauses are a
    single contract, and passing them positionally is how three of them came
    to be silently skipped."""

    signals: set[str]
    asset_classes: set[str]
    strategies: set[str]
    assets: set[str]
    regime_types: set[str]


def _validate_predicate(predicate: dict[str, Any], registries: Registries) -> str | None:
    signal = predicate.get("signal")
    feature = predicate.get("feature")
    op = predicate.get("op")
    value = predicate.get("value")

    if signal == _REGIME_SIGNAL:
        # 'regime' is a step function of RegimeType ids: only 'type', only
        # equality, and only against an id that exists — an unknown id is
        # WORSE than a crash, it is a condition that silently never matches
        # and leaves the invariant unmaturable for want of moments.
        if feature not in _REGIME_FEATURES:
            return f"regime signal requires feature='type', got {feature!r}"
        if op not in ("==", "!="):
            return f"regime type comparison requires '=='/'!=', got {op!r}"
        if not isinstance(value, str):
            return f"regime type value must be a RegimeType id, got {value!r}"
        if value not in registries.regime_types:
            return f"unknown regime type: {value!r}"
        return None

    if signal not in registries.signals:
        return f"unknown signal: {signal!r}"
    if feature not in _SERIES_FEATURES:
        return f"feature {feature!r} invalid for series signal {signal!r}"
    if op not in _OPS:
        return f"invalid op: {op!r}"
    # "`op`/`value` type-consistent" (docs/ARCHITECTURE.md VALIDATION GATE):
    # level/speed/acceleration are floats, so a non-numeric threshold raises
    # TypeError inside the comparison rather than demoting. `bool` is
    # excluded deliberately — it is an int subclass in Python, and `speed <
    # True` is never what an author meant.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return f"non-numeric value {value!r} for {signal}.{feature}"
    return None


def validate_invariant(
    condition: list[dict[str, Any]],
    effect: dict[str, Any] | None,
    registries: Registries,
) -> str | None:
    """`None` if valid, else the reason it must be DEMOTED to reference
    knowledge (a malformed condition/effect never silently breaks
    maturation).

    Implements every clause of the gate contract in docs/ARCHITECTURE.md.
    They are NOT defensive niceties: each rejected shape otherwise reaches
    the sweep as a KeyError/TypeError (feature, metric, op-value) or as a
    silent never-matching condition (unknown regime type)."""
    for predicate in condition:
        reason = _validate_predicate(predicate, registries)
        if reason is not None:
            return reason
    if effect is None:
        return None
    # A NON-OBJECT EFFECT IS A DEMOTION REASON, not a crash. `spec` is typed
    # `dict[str, Any]`, so Pydantic guarantees the envelope and NOTHING about
    # what sits under `effect` — and on 2008-09-02 of the on-stack M8b run a
    # Worker wrote it as prose. Every other malformed shape here returns a
    # reason; this one reached `effect.get` and raised `AttributeError` out of
    # the whole cognitive cycle, which is exactly the "never silently breaks
    # maturation" promise in this docstring, broken by the one field the
    # function forgot to shape-check.
    if not isinstance(effect, dict):
        return f"invalid effect: expected an object, got {type(effect).__name__}"
    asset_classes, strategy_ids, assets = (
        registries.asset_classes,
        registries.strategies,
        registries.assets,
    )
    handle = effect.get("handle", "")
    method = effect.get("method")
    direction = effect.get("direction")
    metric = effect.get("metric")
    if method not in _VALID_METHODS:
        return f"invalid method: {method!r}"
    if direction not in _VALID_DIRECTIONS:
        return f"invalid direction: {direction!r}"
    # "`metric` a computed indicator" (docs/ARCHITECTURE.md VALIDATION GATE).
    # Load-bearing: the confrontation reads `metric` as a COLUMN of the
    # benchmark frames, so an unknown one raises KeyError mid-sweep instead of
    # demoting. 'relative_return' is the near-miss that arrived twice from a
    # real author — plausible, but the RELATIVITY is the method's job
    # (cross_class), not the metric's.
    if metric not in BENCHMARK_METRICS:
        return f"metric {metric!r} is not a computed indicator"
    if handle.startswith("asset-class:"):
        if method not in ("cross_class", "absolute"):
            return f"method {method!r} inconsistent with asset-class handle"
        if handle.split(":", 1)[1] not in asset_classes:
            return f"unknown asset class: {handle!r}"
        # AN ABSOLUTE CLAIM ON CASH IS A CLAIM ABOUT ITS REAL RETURN ("cash
        # loses purchasing power when inflation runs hot"), and `return` is
        # nominal: a bill's nominal return over one horizon never leaves the
        # margin, so every moment is neutral and the claim keeps its starting
        # weight for good — against "nothing stays proposed forever". No
        # real-return indicator is computed (owner, 2026-10-04: too few claims
        # for the work), so the claim is not expressible and is reference
        # knowledge. Cash AGAINST the other classes is measurable and stays.
        if handle == _CASH_HANDLE and method == "absolute" and metric == "return":
            return "an absolute claim on cash is a real-return claim, which is not measured"
    elif handle.startswith("strategy:"):
        if method not in ("cross_strategy", "absolute"):
            return f"method {method!r} inconsistent with strategy handle"
        if handle.split(":", 1)[1] not in strategy_ids:
            return f"unknown strategy: {handle!r}"
    elif handle.startswith("asset:"):
        # "cross_class ⇒ asset/class handle" (docs/ARCHITECTURE.md VALIDATION
        # GATE): an asset handle is compared against the OTHER classes, which
        # is how a single-asset claim ("gold outperforms across asset
        # classes") is stated without diluting it into its blended class.
        if method not in ("cross_class", "absolute"):
            return f"method {method!r} inconsistent with asset handle"
        if handle.split(":", 1)[1] not in assets:
            return f"unknown asset: {handle!r}"
    else:
        return f"unrecognized handle: {handle!r}"
    return None


# -- pure core: contradiction check ------------------------------------------


@dataclasses.dataclass(frozen=True)
class ContradictionPair:
    invariant_a: str
    invariant_b: str
    handle: str
    metric: str


def _disjoint(p1: dict[str, Any], p2: dict[str, Any]) -> bool:
    """Conservative pairwise disjointness on a shared (signal, feature):
    `< v1` vs `> v2` (or >=) with v1 <= v2 cannot both hold. Anything else
    (including `==`/`!=` combos) is NOT flagged disjoint — a missed overlap
    is safer than a false 'cannot coexist' (this feeds a review flag, not an
    auto-block)."""
    for opx, vx, opy, vy in (
        (p1["op"], p1["value"], p2["op"], p2["value"]),
        (p2["op"], p2["value"], p1["op"], p1["value"]),
    ):
        if opx in ("<", "<=") and opy in (">", ">=") and vx <= vy:
            return True
    return False


def conditions_can_overlap(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    """Whether two conditions COULD be simultaneously active — 'always'
    (empty) overlaps everything; predicates on different (signal, feature)
    pairs are assumed independent; predicates on the same pair overlap
    unless provably disjoint (`_disjoint`)."""
    if not a or not b:
        return True
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for p in a:
        by_key.setdefault((p["signal"], p["feature"]), []).append(p)
    for p in b:
        key = (p["signal"], p["feature"])
        if any(_disjoint(pa, p) for pa in by_key.get(key, [])):
            return False
    return True


def find_contradictions(
    invariants: list[tuple[str, list[dict[str, Any]], dict[str, Any]]],
) -> list[ContradictionPair]:
    """Pairwise over `(id, condition, effect)` triples (already filtered to
    `status='integrated'` by the caller) — docs/ARCHITECTURE.md 'Invariant
    contradiction check': same handle + same metric, opposing direction,
    conditions that can co-occur."""
    pairs: list[ContradictionPair] = []
    for i in range(len(invariants)):
        id_a, cond_a, eff_a = invariants[i]
        for j in range(i + 1, len(invariants)):
            id_b, cond_b, eff_b = invariants[j]
            if eff_a["handle"] != eff_b["handle"] or eff_a["metric"] != eff_b["metric"]:
                continue
            if eff_a["direction"] == eff_b["direction"]:
                continue
            if conditions_can_overlap(cond_a, cond_b):
                pairs.append(ContradictionPair(id_a, id_b, eff_a["handle"], eff_a["metric"]))
    return pairs


# -- async DB layer (writer path — agent-only, ADR-004/ADR-005) ------------


def _handle_id(handle: str) -> str:
    return handle.split(":", 1)[1]


def _asof(frame: pd.DataFrame, column: str, moment_date: pd.Timestamp) -> float | None:
    eligible = frame.index[frame.index <= moment_date]
    if len(eligible) == 0:
        return None
    return ratios.flt(frame.loc[eligible[-1], column])


def _asof_forward(
    frame: pd.DataFrame, column: str, moment_date: pd.Timestamp, horizon: pd.Timedelta
) -> float | None:
    """The metric over the horizon FOLLOWING `moment_date` — the window that
    actually tests the invariant's claim ("after the condition fired, did the
    handle outperform?").

    `benchmark_valuation` rows carry TRAILING-horizon metrics dated at the
    date they become knowable (backtests.py `period_series_frame`), so the
    forward window `[d, d+horizon]` IS the row at `d+horizon` — read it there
    rather than storing look-ahead under `d` (ADR-003).

    `None` when the series does not yet reach `d+horizon`: the moment's
    outcome window has NOT COMPLETED, and docs/ARCHITECTURE.md only confronts
    a moment "when it COMPLETES". Without this guard the last rows would
    silently be scored on a truncated window."""
    target = moment_date + horizon
    if frame.empty or frame.index.max() < target:
        return None
    return _asof(frame, column, target)


def _median_asof_forward(
    others: dict[str, pd.DataFrame],
    column: str,
    moment_date: pd.Timestamp,
    horizon: pd.Timedelta,
) -> float | None:
    values = [
        v
        for f in others.values()
        if (v := _asof_forward(f, column, moment_date, horizon)) is not None
    ]
    return float(np.median(values)) if values else None


def _excess_at(
    own_frame: pd.DataFrame,
    others: dict[str, pd.DataFrame],
    metric: str,
    method: str,
    moment_date: pd.Timestamp,
    horizon: pd.Timedelta,
) -> float | None:
    """`handle metric - benchmark metric`, both over the horizon FOLLOWING
    `moment_date`. `None` if either side is unavailable (incl. an incomplete
    outcome window — see `_asof_forward`)."""
    handle_value = _asof_forward(own_frame, metric, moment_date, horizon)
    benchmark_value = (
        0.0 if method == "absolute" else _median_asof_forward(others, metric, moment_date, horizon)
    )
    if handle_value is None or benchmark_value is None:
        return None
    return handle_value - benchmark_value


def _all_excess(
    own_frame: pd.DataFrame,
    others: dict[str, pd.DataFrame],
    metric: str,
    method: str,
    horizon: pd.Timedelta,
) -> list[float]:
    """`_excess_at` over EVERY date the benchmark series covers — the
    condition plays no part, which is precisely what makes the median of
    this the invariant's no-condition null (`baseline_excess`).

    Point-in-time (ADR-003): this is the BIRTH sweep, where
    docs/ARCHITECTURE.md already states the resulting market_score is "a
    weight prior, not out-of-sample proof" — a full-sample baseline carries
    the same in-sample bias the sweep already concedes, and no more. The
    FORWARD weekly confrontation (M8) must instead take the baseline as known
    at t, or it would leak look-ahead into the replays."""
    values = [
        v
        for d in own_frame.index
        if (v := _excess_at(own_frame, others, metric, method, d, horizon)) is not None
    ]
    return values


def margin_for_metric(metric: str, thresholds: dict[str, float]) -> float:
    """Per-metric no-op band, falling back to the generic
    `confrontation_margin` for any metric without an explicit override — ONE
    absolute band cannot serve `return` (dispersion ~0.1-1.0) and
    `max_drawdown` (dispersion ~0.04) at once (db/seed_data.py)."""
    return thresholds.get(f"confrontation_margin_{metric}", thresholds["confrontation_margin"])


async def _benchmark_frames(db: InvestmentDB, benchmark_kind: str) -> dict[str, pd.DataFrame]:
    """`benchmark_id -> DataFrame` (date-indexed, weekly rows from
    `benchmark_valuation`) — what `effect.method` reads (cross_class ->
    asset_class kind, cross_strategy -> strategy kind)."""
    rows = await db.query(
        "SELECT benchmark_id, date, return, sortino_rolling, max_drawdown, volatility "
        "FROM benchmark_valuation WHERE benchmark_kind = :kind ORDER BY benchmark_id, date",
        kind=benchmark_kind,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(str(r["benchmark_id"]), []).append(r)
    result: dict[str, pd.DataFrame] = {}
    for bid, items in grouped.items():
        idx = pd.DatetimeIndex([i["date"] for i in items])
        result[bid] = pd.DataFrame(
            {
                "return": [i["return"] for i in items],
                "sortino_rolling": [i["sortino_rolling"] for i in items],
                "max_drawdown": [i["max_drawdown"] for i in items],
                "volatility": [i["volatility"] for i in items],
            },
            index=idx,
        ).sort_index()
    return result


async def _signal_frame(db: InvestmentDB, ticker: str) -> pd.DataFrame:
    rows = await db.query(
        "SELECT ts, level, speed, acceleration FROM market_data WHERE ticker = :t ORDER BY ts",
        t=ticker,
    )
    if not rows:
        return pd.DataFrame(columns=["level", "speed", "acceleration"])
    idx = pd.DatetimeIndex([r["ts"] for r in rows])
    return pd.DataFrame(
        {
            "level": [r["level"] for r in rows],
            "speed": [r["speed"] for r in rows],
            "acceleration": [r["acceleration"] for r in rows],
        },
        index=idx,
    )


async def _regime_type_series(db: InvestmentDB) -> pd.Series:
    """A daily step function of `regime_type_id` — every historical AND the
    current (open-ended) Regime instance, for `feature='type'` predicates."""
    rows = await db.query(
        "SELECT regime_type_id, start_date, end_date FROM regime ORDER BY start_date"
    )
    if not rows:
        return pd.Series(dtype=object)
    today = pd.Timestamp(date.today())
    pieces = []
    for r in rows:
        start = pd.Timestamp(str(r["start_date"]))
        end = pd.Timestamp(str(r["end_date"])) if r["end_date"] else today
        pieces.append(pd.Series(r["regime_type_id"], index=pd.date_range(start, end, freq="D")))
    combined = pd.concat(pieces)
    return combined[~combined.index.duplicated(keep="last")].sort_index()


def _align_daily(
    signal_frames: dict[str, pd.DataFrame], regime_type_series: pd.Series
) -> tuple[dict[str, pd.DataFrame], pd.Series]:
    """Reindex every referenced signal + the regime-type series onto ONE
    common daily calendar, forward-filled — point-in-time: a signal's last
    known reading holds until the next print (no look-ahead, ADR-003)."""
    indices = [f.index for f in signal_frames.values() if not f.empty]
    if not regime_type_series.empty:
        indices.append(regime_type_series.index)
    if not indices:
        return signal_frames, regime_type_series
    calendar = pd.date_range(
        min(idx.min() for idx in indices), max(idx.max() for idx in indices), freq="D"
    )
    aligned = {alias: frame.reindex(calendar).ffill() for alias, frame in signal_frames.items()}
    aligned_regime = (
        regime_type_series.reindex(calendar).ffill()
        if not regime_type_series.empty
        else regime_type_series
    )
    return aligned, aligned_regime


_MATURED_MARKER = " [birth-matured"


def verdict_rule(thresholds: dict[str, float], metric: str | None) -> dict[str, float]:
    """Everything OUTSIDE an invariant that determines its verdict: how the
    effect is measured (horizon, this metric's margin) and where the bars
    sit. Digested into the maturation fingerprint so that changing a rule
    re-matures, exactly as changing a definition does."""
    return {
        "horizon_weeks": thresholds["proposal_outcome_weeks"],
        "margin": margin_for_metric(metric, thresholds) if metric else 0.0,
        "n_min": thresholds["invariant_min_confrontations"],
        "theta": thresholds["invariant_time_validation_score"],
        "refuted_min": thresholds["invariant_refuted_min_confrontations"],
        "refuted_score": thresholds["invariant_refuted_score"],
        "confidence": thresholds["invariant_verdict_confidence"],
        "null": thresholds["invariant_null_score"],
    }


def definition_fingerprint(condition: list[dict[str, Any]], effect: dict[str, Any] | None) -> str:
    """A stable digest of WHAT AN INVARIANT CLAIMS — the (condition, effect)
    pair and nothing else. Stamped on every confrontation
    (`invariant_confrontations.definition`), because evidence belongs to the
    definition it tested: a revised condition must not inherit the forward
    confrontations the old one earned.

    NOT `maturation_fingerprint`, which also hashes the verdict's bars. Those
    decide how evidence is JUDGED, not what it is evidence OF — stamped with
    that one, tightening a threshold would orphan every confrontation ever
    recorded."""
    payload = json.dumps(
        {"condition": condition, "effect": effect}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def stored_definition(row: Mapping[str, Any]) -> str:
    """`definition_fingerprint` of an `invariant` ROW — `condition` and `effect`
    as the JSON text the table holds. One reader of that encoding, because
    every confrontation writer needs the fingerprint and a second parser of
    "empty condition" or "no effect" is a second place to get it wrong."""
    return definition_fingerprint(
        json.loads(row["condition"]) if row["condition"] else [],
        json.loads(row["effect"]) if row["effect"] else None,
    )


def maturation_fingerprint(
    condition: list[dict[str, Any]],
    effect: dict[str, Any] | None,
    rule: dict[str, float],
) -> str:
    """A stable digest of everything a SWEEP is about: the (condition, effect)
    pair, the rule its verdict was earned under, and which outcomes the sweep
    records. `sort_keys` makes it insensitive to key order, so re-serialising
    unchanged inputs never looks like an edit.

    `records` is in here because a sweep that starts storing something it used
    to drop has not been run yet on any invariant matured before: adding
    'neutral' and 'no_data' (2026-10-04) re-sweeps every definition once, and
    so does adding each moment's 'lift'.

    The rule belongs in here for the same reason the definition does. A
    verdict is a claim about evidence measured one way and judged against
    one set of bars; move either and the stored verdict is stale. Keyed on
    the definition alone (the M5 fingerprint), tightening the integration
    bar left every already-matured invariant sitting on the verdict the OLD
    bar gave it — including the ones the new bar exists to catch."""
    payload = json.dumps(
        {
            "condition": condition,
            "effect": effect,
            "rule": rule,
            "records": [CONFIRMED, REFUTED, NEUTRAL, NO_DATA, "lift"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


async def _already_matured(db: InvestmentDB, invariant_id: str, fingerprint: str) -> bool:
    """Has THIS DEFINITION been swept? Moments are swept once at birth
    (docs/ARCHITECTURE.md), so a re-run must not re-sweep — but the verdict
    belongs to the condition/effect it was earned under, and both are
    mutable: `seed._seed_invariants` rewrites them on every run (M7's
    curation consolidation revises them too) while deliberately preserving
    the maturation fields.

    Keyed on a definition FINGERPRINT, not on "was ever matured": the latter
    let an EDITED invariant keep a verdict measured against its old
    condition. Demonstrated on the live DB — rewriting the gold invariant's
    condition to `growth.speed > 999` (which can never fire, so no evidence
    is even possible) preserved its 0.646/integrated, and gate 6 would have
    cited it. An edit now re-matures; an unchanged definition still skips.

    The marker also beats "any invariant_confrontations row exists": a
    condition whose every moment is a no-op leaves ZERO rows, which would
    look identical to never-matured and be reprocessed forever."""
    rows = await db.query(
        "SELECT 1 AS x FROM invariant WHERE id = :id AND trace LIKE :marker LIMIT 1",
        id=invariant_id,
        marker=f"%{_MATURED_MARKER} %def:{fingerprint}%",
    )
    return bool(rows)


async def _force_uncertified(
    db: InvestmentDB, invariant_id: str, reason: str, status: str = "proposed"
) -> None:
    """Whatever the AUTHOR claimed, an invariant the engine did not MEASURE
    is not time-validated (ADR-006: belief does not grant integration,
    history does).

    Load-bearing, not defensive: every path that cannot produce a verdict
    (reference knowledge, demotion, no benchmark) returns before
    `_persist_maturation`, so without this the `status` column keeps whatever
    was written at birth. Authors do supply it — the owner-submitted gold
    invariant arrived with `status: 'integrated'`, `validated_at` set and a
    hand-authored `market_score: 0.78` — and that is exactly the claim this
    engine exists to withhold. `validated_at` is cleared with it: a
    certification timestamp for a certification that never happened is worse
    than none.

    `status` is 'proposed' — awaiting evidence — EXCEPT for reference knowledge,
    which takes `REFERENCE_STATUS`. The two are different states wearing one
    label until now: 'proposed' means "not yet measured", and ADR-006 promises
    nothing stays there forever, whereas reference knowledge has NO condition
    and NO effect and can therefore never be confronted at all. Filing 209 of
    them (the live corpus, 2026-08) as 'proposed' made that promise false for
    88% of the rows carrying it and made every "how many are awaiting evidence"
    count meaningless. A no-benchmark invariant keeps 'proposed': it HAS an
    effect, the system simply cannot measure it yet — that one is genuinely
    waiting."""
    now = datetime.now(UTC).isoformat()
    await db.command(
        # The MEASURED fields go back to their pre-confrontation defaults too,
        # not just `status`: an author who supplies a verdict also supplies
        # the counts and score behind it (the gold invariant arrived with
        # market_score 0.78 and 4/2 counts). Leaving those would let an
        # unmeasured invariant carry a high weight_effective into Worker
        # context ordering while merely being barred from citation.
        # market_score 1.0 = `market_score(0, 0)`, and matches the pinned
        # reference-knowledge rule ("never confronted, market_score stays
        # 1.0" — docs/DATA_MODELS.md).
        "UPDATE invariant SET status = :status, validated_at = NULL, "
        "market_score = 1.0, confirmation_count = 0, infirmation_count = 0, "
        # A REFERENCE NOTE CARRIES NO WEIGHT (owner, 2026-10-04). A weight is
        # belief that evidence replaces; a note can never be confronted, so its
        # weight was the author's reputation, for good — and retrieval ordered
        # by it, so what was never measured outranked what had been. NULL says
        # what is true: not measured. An unmeasured 'proposed' invariant keeps
        # its prior, because its evidence is still to come.
        "weight_effective = CASE WHEN :status_is = :reference THEN NULL "
        "ELSE MAX(weight_initial, floor_weight) END, "
        "trace = trace || :suffix, updated_at = :now WHERE id = :id",
        status=status,
        status_is=status,
        reference=REFERENCE_STATUS,
        suffix=f" [NOT CERTIFIED: {reason}]",
        now=now,
        id=invariant_id,
    )


async def _demote_to_reference(db: InvestmentDB, invariant_id: str, reason: str) -> None:
    """Reference knowledge: never confronted, market_score frozen at 1.0
    (docs/DATA_MODELS.md). It is NOT thereby certified — `_force_uncertified`
    is the caller's companion, since gate 6 cites `integrated` only."""
    now = datetime.now(UTC).isoformat()
    await db.command(
        "UPDATE invariant SET condition = '[]', effect = NULL, market_score = 1.0, "
        "trace = trace || :suffix, updated_at = :now WHERE id = :id",
        suffix=f" [DEMOTED to reference knowledge: {reason}]",
        now=now,
        id=invariant_id,
    )


async def restate_invariant(
    db: InvestmentDB, invariant_id: str, thresholds: dict[str, float], as_of: date
) -> str:
    """An invariant's STANDING, re-derived from its evidence: score, weight and
    verdict written together, and the verdict returned.

    IT COUNTS THE EVIDENCE ITSELF, from the MEASURED confrontations (birth and
    forward sweeps) of the definition in force (`definition_fingerprint`). A
    reading — `source='evaluation'` — is not evidence and is not counted.
    It used to be handed two counters, and
    each caller made them its own way: the evaluation and proposal paths added
    one to the stored count, the birth sweep overwrote it with its backtest
    rows alone — dropping every forward confrontation until the next weekly
    restatement — and none of them could tell a row earned under a revised
    condition from one earned under the current one.

    ONE WRITER BECAUSE THERE WERE THREE, and they disagreed. The verdict is
    stateless — "recomputed from current counts at every confrontation"
    (`time_validation_verdict`) — but the evaluation and proposal paths each
    carried their own UPDATE and neither wrote `status`, so a confrontation
    moved the score and left the verdict of the previous Sunday's weekly
    restatement standing for up to a week. `validated_at` follows the same rule
    as at birth: set on the first integration, cleared when it ends.

    Whatever the table holds is what is counted, which is what makes the as-of
    replay point-in-time: its snapshot keeps only the confrontations whose
    outcome was knowable at t (`db/as_of_snapshot.py`, on `available_at`)."""
    row = (
        await db.query(
            "SELECT weight_initial, floor_weight, condition, effect FROM invariant WHERE id = :id",
            id=invariant_id,
        )
    )[0]
    definition = stored_definition(row)
    tally = (
        await db.query(
            "SELECT COALESCE(SUM(verdict = :confirmed), 0) AS confirmations, "
            " COALESCE(SUM(verdict = :refuted), 0) AS infirmations "
            "FROM invariant_confrontations "
            "WHERE invariant_id = :id AND definition = :definition "
            "AND source IN (:birth, :forward)",
            confirmed=CONFIRMED,
            refuted=REFUTED,
            id=invariant_id,
            definition=definition,
            birth=BIRTH_SOURCE,
            forward=FORWARD_SOURCE,
        )
    )[0]
    confirmations, infirmations = int(tally["confirmations"]), int(tally["infirmations"])
    score, weight = compute_weight_update(
        float(row["weight_initial"]), float(row["floor_weight"]), confirmations, infirmations
    )
    status = time_validation_verdict(
        confirmations,
        infirmations,
        score,
        n_min=thresholds["invariant_min_confrontations"],
        theta=thresholds["invariant_time_validation_score"],
        refuted_min_confrontations=thresholds["invariant_refuted_min_confrontations"],
        refuted_score=thresholds["invariant_refuted_score"],
        verdict_confidence=thresholds["invariant_verdict_confidence"],
        null_score=thresholds["invariant_null_score"],
    )
    await db.command(
        "UPDATE invariant SET confirmation_count = :cc, infirmation_count = :ic, "
        "market_score = :score, weight_effective = :weff, status = :status, "
        # Set on the FIRST integration and held (COALESCE) while it lasts,
        # cleared the moment it ends — the verdict is stateless, so
        # 'integrated' is not a ratchet and `validated_at` must not be one
        # either ("null while still a candidate", docs/DATA_MODELS.md).
        "validated_at = CASE WHEN :status2 = 'integrated' "
        "THEN COALESCE(validated_at, :as_of) ELSE NULL END, "
        "updated_at = :now WHERE id = :id",
        cc=confirmations,
        ic=infirmations,
        score=score,
        weff=weight,
        status=status,
        status2=status,
        as_of=as_of.isoformat(),
        now=datetime.now(UTC).isoformat(),
        id=invariant_id,
    )
    return status


async def _persist_maturation(
    db: InvestmentDB,
    invariant_id: str,
    confrontation_rows: list[dict[str, Any]],
    definition: str,
    fingerprint: str,
    thresholds: dict[str, float],
) -> str:
    """The sweep's rows in, the invariant's standing restated, the verdict
    returned — one transaction."""
    today = date.today()
    async with db.transaction():
        # The sweep REPLACES every mechanical row, the forward ones included.
        # It runs again for one of three reasons — the definition was edited,
        # the rule changed (margin, horizon), or the data was repaired — and
        # each makes a forward row stale in the same way as a birth row: it
        # answers a question that is no longer the one asked. The sweep covers
        # those dates again, so keeping them would count one moment twice.
        # A reading or a proposal outcome is not ours to discard, and needs no
        # discarding: it carries the definition it tested, and
        # `restate_invariant` counts the current one only.
        await db.command(
            "DELETE FROM invariant_confrontations "
            "WHERE invariant_id = :id AND source IN (:birth, :forward)",
            id=invariant_id,
            birth=BIRTH_SOURCE,
            forward=FORWARD_SOURCE,
        )
        # Forward rows written before `definition` existed (ADDED_COLUMNS,
        # 2026-10-04) carry NULL. Nothing recorded which definition they
        # tested, so they are attributed to the one this sweep finds in force:
        # the first maturation after the column arrived is the last moment at
        # which "the current one" is still the best available answer.
        await db.command(
            "UPDATE invariant_confrontations SET definition = :definition "
            "WHERE invariant_id = :id AND definition IS NULL",
            definition=definition,
            id=invariant_id,
        )
        for row in confrontation_rows:
            await db.command(
                "INSERT INTO invariant_confrontations "
                "(id, invariant_id, moment_context, signal_date, available_at, verdict, "
                " lift, source, source_id, definition) "
                "VALUES (:id, :invariant_id, :moment_context, :signal_date, :available_at, "
                " :verdict, :lift, :source, :source_id, :definition)",
                **row,
            )
        status = await restate_invariant(db, invariant_id, thresholds, today)
        standing = (
            await db.query(
                "SELECT market_score, confirmation_count + infirmation_count AS n "
                "FROM invariant WHERE id = :id",
                id=invariant_id,
            )
        )[0]
        await db.command(
            "UPDATE invariant SET trace = trace || :marker WHERE id = :id",
            # The audit trail of the verdict, not just its date: a 'rejected'
            # status is not disputable without the evidence it was based on.
            marker=(
                f"{_MATURED_MARKER} {today.isoformat()} def:{fingerprint}: {status}, "
                f"score={float(standing['market_score']):.3f}, N={int(standing['n'])}]"
            ),
            id=invariant_id,
        )
    return status


@dataclasses.dataclass(frozen=True)
class MeasurementInputs:
    """Everything a sweep reads to measure an invariant, loaded once for all of
    them: the rule, the condition's signals on one daily calendar, and the
    benchmark series an effect is read against. Shared by the birth sweep and
    the forward sweep so that the two cannot measure one claim two ways."""

    thresholds: dict[str, float]
    horizon: pd.Timedelta
    signal_frames: dict[str, pd.DataFrame]
    regime_type_series: pd.Series
    benchmark_asset_class: dict[str, pd.DataFrame]
    benchmark_strategy: dict[str, pd.DataFrame]
    benchmark_asset: dict[str, pd.DataFrame]
    asset_to_class: dict[str, str]
    registries: Registries


async def _load_measurement_inputs(
    db: InvestmentDB, invariant_rows: list[dict[str, Any]]
) -> MeasurementInputs:
    threshold_rows = await db.query("SELECT key, value FROM system_thresholds")
    thresholds = {r["key"]: r["value"] for r in threshold_rows}
    # The effect is measured over the horizon FOLLOWING a condition-moment;
    # reusing proposal_outcome_weeks keeps the two confrontation sources
    # (backtest here, proposal in outcomes.py at M8) on ONE horizon, so their
    # verdicts mean the same thing.
    horizon = pd.Timedelta(weeks=thresholds["proposal_outcome_weeks"])

    asset_to_class = await investable_tickers(db)
    registries = Registries(
        signals=set(SIGNAL_ALIASES),
        asset_classes=set(BENCHMARK_CLASSES),
        strategies={str(r["id"]) for r in await db.query("SELECT id FROM strategy")},
        assets=set(asset_to_class),
        regime_types={str(r["id"]) for r in await db.query("SELECT id FROM regime_type")},
    )

    needed_aliases: set[str] = set()
    for inv in invariant_rows:
        condition = json.loads(inv["condition"]) if inv["condition"] else []
        needed_aliases.update(p["signal"] for p in condition if p["signal"] != "regime")
    signal_frames = {
        alias: await _signal_frame(db, SIGNAL_ALIASES[alias])
        for alias in needed_aliases
        if alias in SIGNAL_ALIASES
    }
    signal_frames, regime_type_series = _align_daily(signal_frames, await _regime_type_series(db))

    return MeasurementInputs(
        thresholds=thresholds,
        horizon=horizon,
        signal_frames=signal_frames,
        regime_type_series=regime_type_series,
        benchmark_asset_class=await _benchmark_frames(db, BENCHMARK_KIND_ASSET_CLASS),
        benchmark_strategy=await _benchmark_frames(db, BENCHMARK_KIND_STRATEGY),
        benchmark_asset=await _benchmark_frames(db, BENCHMARK_KIND_ASSET),
        asset_to_class=asset_to_class,
        registries=registries,
    )


def _benchmark_for(
    effect: dict[str, Any], inputs: MeasurementInputs
) -> tuple[pd.DataFrame | None, dict[str, pd.DataFrame]]:
    """The handle's own series and the series it is compared against."""
    method, handle = effect["method"], effect["handle"]
    handle_id = _handle_id(handle)
    if method == "cross_strategy" or (method == "absolute" and handle.startswith("strategy:")):
        strategies = inputs.benchmark_strategy
        return strategies.get(handle_id), {b: f for b, f in strategies.items() if b != handle_id}
    classes = inputs.benchmark_asset_class
    if handle.startswith("asset:"):
        # An asset is compared against the OTHER classes — excluding the one
        # it belongs to, which contains it (GLD vs 'gold-commodities' would
        # be partly GLD against itself, and against DJP, which the invariant
        # does not claim anything about).
        own_class = inputs.asset_to_class.get(handle_id)
        return inputs.benchmark_asset.get(handle_id), {
            b: f for b, f in classes.items() if b != own_class
        }
    return classes.get(handle_id), {b: f for b, f in classes.items() if b != handle_id}


def _condition_active(
    condition: list[dict[str, Any]], own_frame: pd.DataFrame, inputs: MeasurementInputs
) -> pd.Series:
    """The daily 'condition holds' series a sweep samples its moments from."""
    if is_absolute_claim(condition):
        # 'always' is just a condition active on every date — same sampler,
        # no special case (and no cliff between it and a near-always
        # condition; see `sample_moments`).
        return pd.Series(True, index=own_frame.index)
    needed = {p["signal"] for p in condition if p["signal"] != "regime"}
    frames = {s: inputs.signal_frames[s] for s in needed if s in inputs.signal_frames}
    return evaluate_condition(condition, frames, inputs.regime_type_series)


@dataclasses.dataclass(frozen=True)
class MaturationResult:
    invariant_id: str
    confirmations: int
    infirmations: int
    neutral: int  # moments measured and found inside the margin band
    market_score: float
    status: str
    skipped_reason: (
        str | None
    )  # 'already_matured' | 'reference_knowledge' | 'demoted' | 'no_benchmark' | None
    # The no-condition null the verdict was measured against (0.0 for an
    # 'always' condition, which is scored absolutely). Reported so the seed
    # inventory shows WHAT the score was relative to — a market_score is not
    # auditable without it.
    baseline: float = 0.0
    # Moments that could not be measured at all (a missing series, a window not
    # yet complete). Apart from `neutral` because the two say opposite things
    # about the claim: one was tested and did not move, the other was not tested.
    no_data: int = 0


async def _mature_one(
    db: InvestmentDB,
    inv: dict[str, Any],
    inputs: MeasurementInputs,
    remeasure_on_changed_data: bool,
) -> MaturationResult:
    invariant_id = str(inv["id"])
    registries, thresholds, horizon = inputs.registries, inputs.thresholds, inputs.horizon
    condition = json.loads(inv["condition"]) if inv["condition"] else []
    effect = json.loads(inv["effect"]) if inv["effect"] else None

    if effect is None:
        await _force_uncertified(
            db, invariant_id, "reference knowledge: no effect to measure", REFERENCE_STATUS
        )
        return MaturationResult(invariant_id, 0, 0, 0, 1.0, REFERENCE_STATUS, "reference_knowledge")

    # VALIDATED BEFORE IT IS INDEXED, and the order was the other way round.
    # `maturation_fingerprint` reads `effect["metric"]` and used to run twenty
    # lines above the validation that guarantees the key exists — so an effect
    # that IS an object but lacks `metric` raised `KeyError` out of the 35y
    # sweep. The sweep runs after `commit_innovations`' loop, outside its
    # per-innovation guard, so one such row would cost the whole cycle.
    #
    # Nothing upstream stops it: the UC8 path writes an invariant WITHOUT
    # calling this function (`writeback._commit_invariant_innovation`), which
    # normalises only a non-object effect (2026-08-09) and passes any dict
    # through. A model that writes `{"handle": "gold"}` is writing a dict.
    #
    # Found by audit rather than by a crash, which is the only reason it is not
    # a third entry in the day's list of the same shape.
    reason = validate_invariant(condition, effect, registries)
    if reason is not None:
        await _demote_to_reference(db, invariant_id, reason)
        await _force_uncertified(db, invariant_id, reason, REFERENCE_STATUS)
        return MaturationResult(invariant_id, 0, 0, 0, 1.0, REFERENCE_STATUS, "demoted")

    fingerprint = maturation_fingerprint(
        condition, effect, verdict_rule(thresholds, effect["metric"])
    )

    if not remeasure_on_changed_data and await _already_matured(db, invariant_id, fingerprint):
        return MaturationResult(
            invariant_id,
            int(inv["confirmation_count"]),
            int(inv["infirmation_count"]),
            0,
            float(inv["market_score"]),
            str(inv["status"]),
            "already_matured",
        )

    method = effect["method"]
    handle = effect["handle"]
    own_frame, others = _benchmark_for(effect, inputs)
    if own_frame is None or own_frame.empty or not others:
        await _force_uncertified(db, invariant_id, f"no benchmark for handle {handle!r}")
        return MaturationResult(invariant_id, 0, 0, 0, 1.0, "proposed", "no_benchmark")

    moment_dates = sample_moments(_condition_active(condition, own_frame, inputs), horizon)

    metric, direction = effect["metric"], effect["direction"]
    margin = margin_for_metric(metric, thresholds)
    tally = dict.fromkeys((CONFIRMED, REFUTED, NEUTRAL, NO_DATA), 0)
    confrontation_rows: list[dict[str, Any]] = []
    descriptor = condition_descriptor(condition)
    definition = definition_fingerprint(condition, effect)

    # The invariant's own no-condition null. Confirmation then means "the
    # effect beat what this handle does ANYWAY", so market_score reads as a
    # skill frequency with a 0.50 null (docs/ARCHITECTURE.md "Invariant
    # confrontation rule"; `baseline_excess`).
    #
    # Taken over the WHOLE sample, future included, and deliberately (owner,
    # 2026-10-04): the birth sweep judges a past moment with everything known
    # today, which is the advantage of looking back and makes the invariant's
    # record more pertinent. What must not leak is the OUTCOME's date, and that
    # is `available_at` below.
    baseline = baseline_excess(_all_excess(own_frame, others, metric, method, horizon), condition)

    # Moments sample condition-ACTIVE time at horizon spacing (see
    # `sample_moments`); the effect is tested over the horizon FOLLOWING each
    # ("the condition holds — did the handle then beat its own baseline?").
    # Measuring a TRAILING window at the moment scores data predating the
    # condition (the first M5 pass — reproduced the unconditional base rate);
    # anchoring at an episode's CLOSE scores the aftermath of the condition
    # ENDING (the second pass — manufactured anti-signal from mean-reversion).
    for moment_date in moment_dates:
        excess = _excess_at(own_frame, others, metric, method, moment_date, horizon)
        verdict = confront_moment(excess, baseline, direction, margin)
        tally[verdict] += 1
        confrontation_rows.append(
            {
                "id": str(ULID()),
                "invariant_id": invariant_id,
                "moment_context": descriptor,
                "signal_date": moment_date.date().isoformat(),
                # ADR-003 for evidence: the outcome is the horizon FOLLOWING
                # the signal, so it is knowable one horizon later and not
                # before. The as-of replay bounds on this, not on the signal.
                "available_at": (moment_date + horizon).date().isoformat(),
                "verdict": verdict,
                "lift": moment_lift(excess, baseline, direction),
                "source": BIRTH_SOURCE,
                "source_id": None,
                "definition": definition,
            }
        )

    status = await _persist_maturation(
        db, invariant_id, confrontation_rows, definition, fingerprint, thresholds
    )
    confirmations, infirmations = tally[CONFIRMED], tally[REFUTED]
    return MaturationResult(
        invariant_id,
        confirmations,
        infirmations,
        tally[NEUTRAL],
        market_score(confirmations, infirmations),
        status,
        None,
        baseline,
        tally[NO_DATA],
    )


async def mature_seed_invariants(
    db: InvestmentDB, *, remeasure_on_changed_data: bool = False
) -> list[MaturationResult]:
    """UC0 step 11b (docs/USE_CASES.md) — `mature_invariant()` on every
    invariant, the SAME factored, source-blind mechanism later applied to
    every post-launch birth (seed invariants are just the first batch).
    Prerequisite: step 10 (regime instances) + step 10b (benchmark_valuation)
    + the market TS must already be persisted.

    `remeasure_on_changed_data` sweeps every measurable invariant again even
    though its definition and rule are unchanged. The fingerprint that decides
    "already matured" knows what was ASKED and how it was judged, not what it
    was measured ON: when a price history is repaired (DJP, 2026-10-04 — a
    phantom -71% day sat in it for two weeks), the stored records are the
    answer to a question put to data that no longer exists."""
    invariant_rows = await db.query("SELECT * FROM invariant ORDER BY id")
    inputs = await _load_measurement_inputs(db, invariant_rows)
    return [await _mature_one(db, inv, inputs, remeasure_on_changed_data) for inv in invariant_rows]


@dataclasses.dataclass(frozen=True)
class ForwardSweepResult:
    """One weekly forward sweep. `waiting` is reported because it is the other
    half of the count: a moment sampled and not yet confronted is the reason a
    quiet week wrote nothing, and without it "nothing was due" and "nothing was
    looked at" read the same."""

    invariants_swept: int
    invariants_confronted: int
    confirmed: int
    refuted: int
    neutral: int
    no_data: int
    waiting: int  # moments whose outcome window has not completed yet


def _baseline_knowable_at(
    knowable: pd.Timestamp,
    own_frame: pd.DataFrame,
    others: dict[str, pd.DataFrame],
    effect: dict[str, Any],
    condition: list[dict[str, Any]],
    horizon: pd.Timedelta,
) -> float:
    """The no-condition null AS IT STOOD the day a forward moment's window
    completed (owner decision D1, 2026-10-04). The birth sweep takes its
    baseline over the whole sample because it looks back; a forward moment is
    judged when it happens, on what was known then. Bounded on the moment's own
    date rather than on the day the chain ran, so that a sweep catching up
    after three weeks asleep writes what three weekly sweeps would have."""
    return baseline_excess(
        _all_excess(
            own_frame.loc[:knowable],
            {bid: frame.loc[:knowable] for bid, frame in others.items()},
            effect["metric"],
            effect["method"],
            horizon,
        ),
        condition,
    )


async def _forward_rows(
    db: InvestmentDB, inv: dict[str, Any], inputs: MeasurementInputs
) -> tuple[list[dict[str, Any]], int] | None:
    """The confrontations one invariant has newly earned, and how many of its
    moments are still waiting for their window. `None` when the forward sweep
    has nothing to say about it: not measurable, or not yet swept at birth
    under its current definition and rule (that sweep comes first, and covers
    everything up to the day it runs)."""
    invariant_id = str(inv["id"])
    condition = json.loads(inv["condition"]) if inv["condition"] else []
    effect = json.loads(inv["effect"]) if inv["effect"] else None
    if effect is None or validate_invariant(condition, effect, inputs.registries) is not None:
        return None
    metric = effect["metric"]
    fingerprint = maturation_fingerprint(condition, effect, verdict_rule(inputs.thresholds, metric))
    if not await _already_matured(db, invariant_id, fingerprint):
        return None
    own_frame, others = _benchmark_for(effect, inputs)
    if own_frame is None or own_frame.empty or not others:
        return None

    horizon = inputs.horizon
    definition = definition_fingerprint(condition, effect)
    stored = await db.query(
        "SELECT signal_date, verdict, source FROM invariant_confrontations "
        "WHERE invariant_id = :id AND definition = :definition "
        "AND source IN (:birth, :forward) ORDER BY signal_date",
        id=invariant_id,
        definition=definition,
        birth=BIRTH_SOURCE,
        forward=FORWARD_SOURCE,
    )

    # TWO KINDS OF MOMENT ARE DUE. The birth sweep sampled up to the day it ran
    # and stored its last moments as 'no_data', their window still open: those
    # are taken up here once the window completes, and they are recognised by
    # position — past the last moment that was measured — so that a moment
    # unmeasurable for want of a series, decades back, is not asked again every
    # week. Then the sampler RESUMES one horizon after the last stored moment,
    # which is why every moment is stored, measured or not: the spacing that
    # keeps the windows disjoint has to survive from one week to the next.
    confronted_forward = {str(r["signal_date"]) for r in stored if r["source"] == FORWARD_SOURCE}
    measured_dates = [str(r["signal_date"]) for r in stored if r["verdict"] != NO_DATA]
    last_measured = max(measured_dates, default="")
    left_open = [
        pd.Timestamp(str(r["signal_date"]))
        for r in stored
        if r["source"] == BIRTH_SOURCE
        and r["verdict"] == NO_DATA
        and str(r["signal_date"]) > last_measured
        and str(r["signal_date"]) not in confronted_forward
    ]
    active = _condition_active(condition, own_frame, inputs)
    if stored:
        last_stored = pd.Timestamp(max(str(r["signal_date"]) for r in stored))
        active = active[active.index >= last_stored + horizon]
    resumed = sample_moments(active, horizon)

    direction = effect["direction"]
    margin = margin_for_metric(metric, inputs.thresholds)
    descriptor = condition_descriptor(condition)
    data_reaches = own_frame.index.max()
    rows: list[dict[str, Any]] = []
    waiting = 0
    for moment_date in [*left_open, *resumed]:
        knowable = moment_date + horizon
        if knowable > data_reaches:
            # Not written: it is confronted ONCE, when its window completes.
            waiting += 1
            continue
        excess = _excess_at(own_frame, others, metric, effect["method"], moment_date, horizon)
        if excess is None and moment_date in left_open:
            continue  # still not measurable, and already recorded as such
        baseline = _baseline_knowable_at(knowable, own_frame, others, effect, condition, horizon)
        verdict = confront_moment(excess, baseline, direction, margin)
        rows.append(
            {
                "id": str(ULID()),
                "invariant_id": invariant_id,
                "moment_context": descriptor,
                "signal_date": moment_date.date().isoformat(),
                "available_at": knowable.date().isoformat(),
                "verdict": verdict,
                "lift": moment_lift(excess, baseline, direction),
                "source": FORWARD_SOURCE,
                "source_id": None,
                "definition": definition,
            }
        )
    return rows, waiting


async def confront_completed_moments(db: InvestmentDB, today: date) -> ForwardSweepResult:
    """The weekly FORWARD sweep (docs/ARCHITECTURE.md "Forward confrontation"):
    every moment whose outcome window has completed since the last sweep is
    confronted, once, and the invariants it touches are restated.

    WHY IT EXISTS. "35 years at birth, then forward" was the stated intention
    and only the first half was mechanical: a matured definition is skipped by
    the birth sweep, the weekly restatement only recounts the rows it finds, so
    an invariant's mechanical record stopped the day it was born and the only
    evidence arriving afterwards was the Worker's reading.

    Idempotent: a moment is written when its window completes and the sampler
    resumes after the last one stored, so a second run finds nothing due — and
    `ux_confrontation_mechanical_moment` refuses the row if it ever did.

    Prerequisite: this week's `benchmark_valuation` rows and derived signals
    (`backtests.materialize_benchmark_valuation`). On stale series nothing ever
    completes."""
    invariant_rows = await db.query(
        "SELECT * FROM invariant WHERE status != :reference ORDER BY id",
        reference=REFERENCE_STATUS,
    )
    inputs = await _load_measurement_inputs(db, invariant_rows)

    rows: list[dict[str, Any]] = []
    swept = waiting = 0
    for inv in invariant_rows:
        earned = await _forward_rows(db, inv, inputs)
        if earned is None:
            continue
        swept += 1
        rows.extend(earned[0])
        waiting += earned[1]

    tally = dict.fromkeys((CONFIRMED, REFUTED, NEUTRAL, NO_DATA), 0)
    for row in rows:
        tally[row["verdict"]] += 1
    confronted = sorted({str(row["invariant_id"]) for row in rows})
    result = ForwardSweepResult(
        invariants_swept=swept,
        invariants_confronted=len(confronted),
        confirmed=tally[CONFIRMED],
        refuted=tally[REFUTED],
        neutral=tally[NEUTRAL],
        no_data=tally[NO_DATA],
        waiting=waiting,
    )
    logger.info("invariant forward sweep %s: %s", today, result)
    if not rows:
        return result

    async with db.transaction():
        # EventLog append precedes the writes it describes (CLAUDE.md "EventLog").
        await db.append_event(
            type=CONFRONTATION_EVENT,
            source_uc=SOURCE_UC,
            source_id=None,
            payload={"source": FORWARD_SOURCE, **dataclasses.asdict(result)},
            event_date=today,
        )
        for row in rows:
            await db.command(
                "INSERT INTO invariant_confrontations "
                "(id, invariant_id, moment_context, signal_date, available_at, verdict, "
                " lift, source, source_id, definition) "
                "VALUES (:id, :invariant_id, :moment_context, :signal_date, :available_at, "
                " :verdict, :lift, :source, :source_id, :definition)",
                **row,
            )
        for invariant_id in confronted:
            await restate_invariant(db, invariant_id, inputs.thresholds, today)
    return result


# -- what a record says beyond its weight ------------------------------------


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """The 95% Wilson interval on a confirmation rate, or `None` with nothing
    decided.

    FOR DISPLAY, NEVER FOR THE VERDICT — the verdict has its own exact tails
    (`time_validation_verdict`). And it is the interval the record would carry
    IF its moments were independent: moments of one episode are not
    (docs/INVARIANT_IMPROVEMENT_PLAN.md action 3.2), so the true uncertainty is
    wider than this, never narrower. It is shown because a rate with no range
    reads as a fact, and 4 of 5 is not the same knowledge as 40 of 50."""
    if total == 0:
        return None
    rate = successes / total
    denominator = 1 + z * z / total
    centre = (rate + z * z / (2 * total)) / denominator
    half_width = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    half_width /= denominator
    return max(0.0, centre - half_width), min(1.0, centre + half_width)


# Below this many decided moments per half, an early-versus-late comparison is
# two anecdotes.
_MIN_DECIDED_PER_HALF = 2


@dataclasses.dataclass(frozen=True)
class EvidenceSummary:
    """What an invariant's MEASURED record says, beside the one number its
    weight compresses it into: how much evidence, how sure, how large, how
    stable, and how much of what the condition did could be measured at all."""

    metric: str
    confirmed: int
    refuted: int
    neutral: int  # measured, inside the margin
    no_data: int  # sampled, not measurable
    rate_range: tuple[float, float] | None  # `wilson_interval`
    mean_lift: float | None  # over every measured moment, neutral ones included
    worst_lift: float | None
    # Confirmation rate over the earlier and the later half of the decided
    # moments, in date order: a record earned in one era reads differently
    # from one earned across both.
    rate_early: float | None
    rate_late: float | None

    @property
    def decided(self) -> int:
        return self.confirmed + self.refuted

    @property
    def rate(self) -> float | None:
        return self.confirmed / self.decided if self.decided else None


def summarize_evidence(metric: str, moments: list[Mapping[str, Any]]) -> EvidenceSummary:
    """`moments` are an invariant's mechanical confrontations of ONE definition
    (`signal_date`, `verdict`, `lift`)."""
    tally = dict.fromkeys((CONFIRMED, REFUTED, NEUTRAL, NO_DATA), 0)
    for moment in moments:
        tally[str(moment["verdict"])] += 1
    lifts = [float(m["lift"]) for m in moments if m["lift"] is not None]
    decided = sorted(
        (m for m in moments if m["verdict"] in COUNTED_VERDICTS),
        key=lambda m: str(m["signal_date"]),
    )
    half = len(decided) // 2

    def rate_of(part: list[Mapping[str, Any]]) -> float | None:
        if len(part) < _MIN_DECIDED_PER_HALF:
            return None
        return sum(1 for m in part if m["verdict"] == CONFIRMED) / len(part)

    return EvidenceSummary(
        metric=metric,
        confirmed=tally[CONFIRMED],
        refuted=tally[REFUTED],
        neutral=tally[NEUTRAL],
        no_data=tally[NO_DATA],
        rate_range=wilson_interval(tally[CONFIRMED], tally[CONFIRMED] + tally[REFUTED]),
        mean_lift=float(np.mean(lifts)) if lifts else None,
        worst_lift=min(lifts) if lifts else None,
        rate_early=rate_of(decided[:half]),
        rate_late=rate_of(decided[half:]),
    )


def describe_evidence(evidence: EvidenceSummary) -> str:
    """One line, for a prompt, a digest or a table cell. ONE renderer, so the
    Worker, the owner and the dashboard read the same record in the same words.
    A part with nothing to say is left out rather than printed empty."""
    if not evidence.decided:
        parts = ["no decided moment yet"]
    else:
        parts = [f"{evidence.confirmed}/{evidence.decided} confirmed"]
        if evidence.rate_range is not None:
            low, high = evidence.rate_range
            parts[0] += f" (rate {evidence.rate:.2f}, 95% range {low:.2f}-{high:.2f})"
    if evidence.neutral:
        parts.append(f"{evidence.neutral} neutral")
    if evidence.no_data:
        parts.append(f"{evidence.no_data} unmeasurable")
    if evidence.mean_lift is not None and evidence.worst_lift is not None:
        parts.append(
            f"mean lift {evidence.mean_lift:+.3f} on {evidence.metric}, "
            f"worst {evidence.worst_lift:+.3f}"
        )
    if evidence.rate_early is not None and evidence.rate_late is not None:
        parts.append(f"early half {evidence.rate_early:.2f}, late half {evidence.rate_late:.2f}")
    return "; ".join(parts)


async def evidence_summaries(
    db: InvestmentDB, invariant_ids: list[str]
) -> dict[str, EvidenceSummary]:
    """The summary of each MEASURABLE invariant among `invariant_ids`, from the
    mechanical confrontations of its definition in force — the rows
    `restate_invariant` counts, so the summary and the weight it sits beside
    cannot describe two different records. A reference note is absent: it has
    no effect, and nothing was ever measured."""
    if not invariant_ids:
        return {}
    placeholders = ",".join(f":i{n}" for n in range(len(invariant_ids)))
    params = {f"i{n}": iid for n, iid in enumerate(invariant_ids)}
    invariant_rows = await db.query(
        f"SELECT id, condition, effect FROM invariant WHERE id IN ({placeholders}) "
        "AND effect IS NOT NULL",
        **params,
    )
    moments = await db.query(
        "SELECT invariant_id, definition, signal_date, verdict, lift "
        f"FROM invariant_confrontations WHERE invariant_id IN ({placeholders}) "
        "AND source IN (:birth, :forward)",
        birth=BIRTH_SOURCE,
        forward=FORWARD_SOURCE,
        **params,
    )
    by_definition: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for moment in moments:
        key = (str(moment["invariant_id"]), str(moment["definition"]))
        by_definition.setdefault(key, []).append(moment)
    return {
        str(row["id"]): summarize_evidence(
            str(json.loads(row["effect"]).get("metric", "?")),
            by_definition.get((str(row["id"]), stored_definition(row)), []),
        )
        for row in invariant_rows
    }


async def check_contradictions(db: InvestmentDB) -> list[ContradictionPair]:
    """docs/ARCHITECTURE.md 'Invariant contradiction check' — pairwise over
    `status='integrated'` invariants. Surfaced in the seed inventory and, for
    owner review, as a digest alert (`alerts.invariant_contradiction_alert`);
    does not auto-resolve anything."""
    rows = await db.query(
        "SELECT id, condition, effect FROM invariant WHERE status = 'integrated' ORDER BY id"
    )
    parsed = [
        (
            str(r["id"]),
            json.loads(r["condition"]) if r["condition"] else [],
            json.loads(r["effect"]),
        )
        for r in rows
        if r["effect"]
    ]
    return find_contradictions(parsed)
