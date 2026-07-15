"""Invariant confrontation + birth maturation + contradiction check
(docs/TASKS.md Phase 5bis `invariants.py`; docs/ARCHITECTURE.md "Invariant
confrontation rule" / "Birth maturation" / "Invariant contradiction check";
docs/DATA_MODELS.md Invariant entity; CLAUDE.md "Invariant weight model").

M5 scope is the FROM-BACKTESTS branch of the confrontation rule
(`mature_invariant()`, run once per invariant at birth — seed invariants are
just the first batch, docs/USE_CASES.md UC0 step 11b) plus the shared
`compute_weight_update()` primitive every future confrontation source
(evaluation/proposal, M8) funnels into, and the contradiction check (run at
seed after 11b/11c and on every new integrated birth).

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
import math
import operator
import statistics
from collections.abc import Callable
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

# -- pure core: weight formula (CLAUDE.md "Invariant weight model") --------


def market_score(confirmations: int, infirmations: int) -> float:
    """`confirmations / (confirmations + infirmations)`, 1.0 until the first
    confrontation."""
    total = confirmations + infirmations
    return confirmations / total if total > 0 else 1.0


def recency_factor(days_since: int, half_life_days: float) -> float:
    """`0.5 + 0.5 * exp(-days_since / half_life)` — `days_since` must already
    be CONDITION-RELATIVE (0 if the condition is active now, else time since
    it was last active), computed by the caller, not here."""
    return 0.5 + 0.5 * math.exp(-days_since / half_life_days)


def weight_effective(
    weight_initial: float, score: float, recency: float, floor_weight: float
) -> float:
    return max(weight_initial * score * recency, floor_weight)


def compute_weight_update(
    weight_initial: float,
    floor_weight: float,
    confirmations: int,
    infirmations: int,
    days_since: int,
    half_life_days: float,
) -> tuple[float, float, float]:
    """`(market_score, recency_factor, weight_effective)` — the single
    computation every confrontation source (backtest/evaluation/proposal)
    funnels into (docs/ARCHITECTURE.md 'Invariant confrontation rule':
    "update_invariant_weights()")."""
    score = market_score(confirmations, infirmations)
    recency = recency_factor(days_since, half_life_days)
    return score, recency, weight_effective(weight_initial, score, recency, floor_weight)


def wilson_upper(confirmations: int, total: int, confidence: float) -> float:
    """One-sided Wilson score upper bound for the true confirmation rate —
    the pinned interval convention for the verdict (well-behaved at small N
    and extreme rates, stdlib-computable; docs/ARCHITECTURE.md
    TIME-VALIDATION VERDICT)."""
    if total == 0:
        return 1.0
    z = statistics.NormalDist().inv_cdf(confidence)
    p = confirmations / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1.0 - p) / total + z * z / (4 * total * total)) / denominator
    return min(center + margin, 1.0)


def time_validation_verdict(
    confirmations: int,
    infirmations: int,
    score: float,
    n_min: float,
    theta: float,
    refuted_min_confrontations: float,
    refuted_score: float,
    verdict_confidence: float,
) -> str:
    """'integrated' | 'rejected' | 'proposed' (docs/ARCHITECTURE.md "Birth
    maturation" TIME-VALIDATION VERDICT; ADR-006 + its M5 amendment —
    mechanical, no user gate). Three outcomes, checked in order:

    - REFUTED (rejected): the effect actively fails when the condition holds
      (point test — arms fast at small N for clearly harmful invariants).
    - INTEGRATED: point estimate clears theta with at least N_min moments.
    - INADEQUATE (rejected): the Wilson upper bound of the score at
      `verdict_confidence` is below theta — given ample evidence, the
      invariant demonstrably CANNOT reach the bar. This is the branch that
      empties the 0.35..theta dead middle, where 4 of 6 seed invariants
      would otherwise sit 'proposed' forever at any N (e.g. 0.545 on N=354,
      upper bound 0.588) — violating "Nothing stays proposed forever"
      (ADR-006). By construction it cannot fire while score >= theta
      (upper > point estimate), so it never races INTEGRATED.

    'proposed' now means exactly one thing: INSUFFICIENT EVIDENCE — and it
    empties mechanically as confrontations accrue (for a true-null invariant
    the bound crosses theta around N~70). The verdict is STATELESS
    (recomputed from current counts at every confrontation), so a rejection
    is as reversible as the evidence that produced it.

    Refutation is checked first: a refuted invariant is never 'integrated'
    even if it also happens to clear N_min/theta on a stale read (it cannot,
    by construction — theta > refuted_score — but the order documents the
    precedence)."""
    total = confirmations + infirmations
    if total >= refuted_min_confrontations and score < refuted_score:
        return "rejected"
    if total >= n_min and score >= theta:
        return "integrated"
    if (
        total >= refuted_min_confrontations
        and wilson_upper(confirmations, total, verdict_confidence) < theta
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

    NON-OVERLAPPING → the Wilson verdict is sound. Each moment's outcome
    window is [d, d+horizon], so horizon-spacing makes the windows disjoint
    and the moments quasi-independent — which is exactly what the binomial
    bound in `time_validation_verdict` assumes. Sampling active time weekly
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


def confront_moment(
    handle_value: float | None, benchmark_value: float | None, direction: str, margin: float
) -> str | None:
    """'confirmed' | 'refuted' | None (no-op: within the margin band, or
    missing data — docs/ARCHITECTURE.md confrontation rule). A metric is
    always "higher is better" as stored (return: higher wins; max_drawdown:
    stored as a negative fraction, less negative = higher = better) — no
    metric-specific sign flip needed."""
    if handle_value is None or benchmark_value is None:
        return None
    diff = handle_value - benchmark_value
    if direction == "outperform":
        if diff > margin:
            return "confirmed"
        if diff < -margin:
            return "refuted"
        return None
    if direction == "underperform":
        if diff < -margin:
            return "confirmed"
        if diff > margin:
            return "refuted"
        return None
    raise ValueError(f"unknown direction: {direction!r}")


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
    if not condition:
        return 0.0
    return float(np.median(excess_values)) if excess_values else 0.0


def condition_descriptor(condition: list[dict[str, Any]]) -> str:
    """`invariant_confrontations.moment_context` for a condition-keyed moment
    (docs/DATA_MODELS.md: "a compact descriptor of the condition that
    held")."""
    if not condition:
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


def definition_fingerprint(condition: list[dict[str, Any]], effect: dict[str, Any] | None) -> str:
    """A stable digest of the (condition, effect) pair — the ONLY thing a
    verdict is about. `sort_keys` makes it insensitive to key order, so
    re-serialising an unchanged definition never looks like an edit."""
    payload = json.dumps(
        {"condition": condition, "effect": effect}, sort_keys=True, separators=(",", ":")
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


async def _force_uncertified(db: InvestmentDB, invariant_id: str, reason: str) -> None:
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
    than none."""
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
        "UPDATE invariant SET status = 'proposed', validated_at = NULL, "
        "market_score = 1.0, confirmation_count = 0, infirmation_count = 0, "
        "weight_effective = MAX(weight_initial * recency_factor, floor_weight), "
        "trace = trace || :suffix, updated_at = :now WHERE id = :id",
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


async def _persist_maturation(
    db: InvestmentDB,
    invariant_id: str,
    confrontation_rows: list[dict[str, Any]],
    confirmations: int,
    infirmations: int,
    score: float,
    recency: float,
    w_eff: float,
    status: str,
    fingerprint: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    today = date.today().isoformat()
    async with db.transaction():
        # The birth sweep REPLACES its own prior output: re-maturing an edited
        # definition must not stack new confirmations on top of rows measured
        # against the old condition. Only source='backtest' (this sweep) is
        # cleared — evaluation/proposal confrontations (M8) are forward
        # evidence and are not ours to discard.
        await db.command(
            "DELETE FROM invariant_confrontations WHERE invariant_id = :id AND source = 'backtest'",
            id=invariant_id,
        )
        for row in confrontation_rows:
            await db.command(
                "INSERT INTO invariant_confrontations "
                "(id, invariant_id, moment_context, date, verdict, severity, source, source_id) "
                "VALUES (:id, :invariant_id, :moment_context, :date, :verdict, :severity, "
                " :source, :source_id)",
                **row,
            )
        await db.command(
            "UPDATE invariant SET confirmation_count = :cc, infirmation_count = :ic, "
            "market_score = :score, recency_factor = :recency, weight_effective = :weff, "
            "status = :status, "
            "validated_at = CASE WHEN :status2 = 'integrated' AND validated_at IS NULL "
            "THEN :today ELSE validated_at END, "
            "trace = trace || :marker, "
            "updated_at = :now WHERE id = :id",
            cc=confirmations,
            ic=infirmations,
            score=score,
            recency=recency,
            weff=w_eff,
            status=status,
            status2=status,
            today=today,
            # The audit trail of the verdict, not just its date: a 'rejected'
            # status is not disputable without the evidence it was based on.
            marker=(
                f"{_MATURED_MARKER} {today} def:{fingerprint}: {status}, "
                f"score={score:.3f}, N={confirmations + infirmations}]"
            ),
            now=now,
            id=invariant_id,
        )


@dataclasses.dataclass(frozen=True)
class MaturationResult:
    invariant_id: str
    confirmations: int
    infirmations: int
    no_ops: int
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


async def _mature_one(
    db: InvestmentDB,
    inv: dict[str, Any],
    signal_frames: dict[str, pd.DataFrame],
    regime_type_series: pd.Series,
    benchmark_asset_class: dict[str, pd.DataFrame],
    benchmark_strategy: dict[str, pd.DataFrame],
    benchmark_asset: dict[str, pd.DataFrame],
    asset_to_class: dict[str, str],
    registries: Registries,
    thresholds: dict[str, float],
    horizon: pd.Timedelta,
    half_life: float,
    n_min: float,
    theta: float,
    refuted_min: float,
    refuted_score: float,
    verdict_confidence: float,
) -> MaturationResult:
    invariant_id = str(inv["id"])
    condition = json.loads(inv["condition"]) if inv["condition"] else []
    effect = json.loads(inv["effect"]) if inv["effect"] else None

    fingerprint = definition_fingerprint(condition, effect)

    if effect is None:
        await _force_uncertified(db, invariant_id, "reference knowledge: no effect to measure")
        return MaturationResult(invariant_id, 0, 0, 0, 1.0, "proposed", "reference_knowledge")

    if await _already_matured(db, invariant_id, fingerprint):
        return MaturationResult(
            invariant_id,
            int(inv["confirmation_count"]),
            int(inv["infirmation_count"]),
            0,
            float(inv["market_score"]),
            str(inv["status"]),
            "already_matured",
        )

    reason = validate_invariant(condition, effect, registries)
    if reason is not None:
        await _demote_to_reference(db, invariant_id, reason)
        await _force_uncertified(db, invariant_id, reason)
        return MaturationResult(invariant_id, 0, 0, 0, 1.0, "proposed", "demoted")

    method = effect["method"]
    handle = effect["handle"]
    handle_id = _handle_id(handle)
    if method == "cross_strategy" or (method == "absolute" and handle.startswith("strategy:")):
        own_frame = benchmark_strategy.get(handle_id)
        others = {bid: f for bid, f in benchmark_strategy.items() if bid != handle_id}
    elif handle.startswith("asset:"):
        # An asset is compared against the OTHER classes — excluding the one
        # it belongs to, which contains it (GLD vs 'gold-commodities' would
        # be partly GLD against itself, and against DJP, which the invariant
        # does not claim anything about).
        own_frame = benchmark_asset.get(handle_id)
        own_class = asset_to_class.get(handle_id)
        others = {bid: f for bid, f in benchmark_asset_class.items() if bid != own_class}
    else:
        own_frame = benchmark_asset_class.get(handle_id)
        others = {bid: f for bid, f in benchmark_asset_class.items() if bid != handle_id}
    if own_frame is None or own_frame.empty or not others:
        await _force_uncertified(db, invariant_id, f"no benchmark for handle {handle!r}")
        return MaturationResult(invariant_id, 0, 0, 0, 1.0, "proposed", "no_benchmark")

    if not condition:
        # 'always' is just a condition active on every date — same sampler,
        # no special case (and no cliff between it and a near-always
        # condition; see `sample_moments`).
        active = pd.Series(True, index=own_frame.index)
    else:
        needed = {p["signal"] for p in condition if p["signal"] != "regime"}
        frames_for_condition = {s: signal_frames[s] for s in needed if s in signal_frames}
        active = evaluate_condition(condition, frames_for_condition, regime_type_series)
    moment_dates = sample_moments(active, horizon)
    active_now = bool(active.iloc[-1]) if len(active) else False
    # Dormancy counts from the last day the condition HELD — NOT from the
    # last sampled moment, which can trail it by up to a horizon; a
    # long-running condition must not read as stale while still active
    # (recency is CONDITION-RELATIVE, docs/DATA_MODELS.md).
    active_days = active.index[active.fillna(False).to_numpy(dtype=bool)]
    last_active_day = active_days[-1] if len(active_days) else None

    metric, direction = effect["metric"], effect["direction"]
    margin = margin_for_metric(metric, thresholds)
    confirmations = infirmations = no_ops = 0
    confrontation_rows: list[dict[str, Any]] = []
    descriptor = condition_descriptor(condition)

    # The invariant's own no-condition null. Confirmation then means "the
    # effect beat what this handle does ANYWAY", so market_score reads as a
    # skill frequency with a 0.50 null (docs/ARCHITECTURE.md "Invariant
    # confrontation rule"; `baseline_excess`).
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
        if verdict is None:
            no_ops += 1
            continue
        if verdict == "confirmed":
            confirmations += 1
        else:
            infirmations += 1
        confrontation_rows.append(
            {
                "id": str(ULID()),
                "invariant_id": invariant_id,
                "moment_context": descriptor,
                "date": moment_date.date().isoformat(),
                "verdict": verdict,
                "severity": 1.0,
                "source": "backtest",
                "source_id": None,
            }
        )

    days_since = (
        0
        if (active_now or last_active_day is None)
        else (pd.Timestamp(date.today()) - last_active_day).days
    )
    weight_initial, floor_weight = float(inv["weight_initial"]), float(inv["floor_weight"])
    score, recency, w_eff = compute_weight_update(
        weight_initial, floor_weight, confirmations, infirmations, max(days_since, 0), half_life
    )
    status = time_validation_verdict(
        confirmations,
        infirmations,
        score,
        n_min,
        theta,
        refuted_min,
        refuted_score,
        verdict_confidence,
    )

    await _persist_maturation(
        db,
        invariant_id,
        confrontation_rows,
        confirmations,
        infirmations,
        score,
        recency,
        w_eff,
        status,
        fingerprint,
    )
    return MaturationResult(
        invariant_id, confirmations, infirmations, no_ops, score, status, None, baseline
    )


async def mature_seed_invariants(db: InvestmentDB) -> list[MaturationResult]:
    """UC0 step 11b (docs/USE_CASES.md) — `mature_invariant()` on every
    invariant, the SAME factored, source-blind mechanism later applied to
    every post-launch birth (seed invariants are just the first batch).
    Prerequisite: step 10 (regime instances) + step 10b (benchmark_valuation)
    + the market TS must already be persisted."""
    threshold_rows = await db.query("SELECT key, value FROM system_thresholds")
    thresholds = {r["key"]: r["value"] for r in threshold_rows}
    # The effect is measured over the horizon FOLLOWING a condition-moment;
    # reusing proposal_outcome_weeks keeps the two confrontation sources
    # (backtest here, proposal in outcomes.py at M8) on ONE horizon, so their
    # verdicts mean the same thing.
    horizon = pd.Timedelta(weeks=thresholds["proposal_outcome_weeks"])
    half_life = thresholds["recency_half_life_days"]
    n_min = thresholds["invariant_min_confrontations"]
    theta = thresholds["invariant_time_validation_score"]
    refuted_min = thresholds["invariant_refuted_min_confrontations"]
    refuted_score = thresholds["invariant_refuted_score"]
    verdict_confidence = thresholds["invariant_verdict_confidence"]

    invariant_rows = await db.query("SELECT * FROM invariant ORDER BY id")

    benchmark_asset_class = await _benchmark_frames(db, BENCHMARK_KIND_ASSET_CLASS)
    benchmark_strategy = await _benchmark_frames(db, BENCHMARK_KIND_STRATEGY)
    benchmark_asset = await _benchmark_frames(db, BENCHMARK_KIND_ASSET)
    asset_to_class = await investable_tickers(db)
    registries = Registries(
        signals=set(SIGNAL_ALIASES),
        asset_classes=set(BENCHMARK_CLASSES),
        strategies={str(r["id"]) for r in await db.query("SELECT id FROM strategy")},
        assets=set(asset_to_class),
        regime_types={str(r["id"]) for r in await db.query("SELECT id FROM regime_type")},
    )
    regime_type_series = await _regime_type_series(db)

    needed_aliases: set[str] = set()
    for inv in invariant_rows:
        condition = json.loads(inv["condition"]) if inv["condition"] else []
        needed_aliases.update(p["signal"] for p in condition if p["signal"] != "regime")
    signal_frames = {
        alias: await _signal_frame(db, SIGNAL_ALIASES[alias])
        for alias in needed_aliases
        if alias in SIGNAL_ALIASES
    }
    signal_frames, regime_type_series = _align_daily(signal_frames, regime_type_series)

    results = []
    for inv in invariant_rows:
        result = await _mature_one(
            db,
            inv,
            signal_frames,
            regime_type_series,
            benchmark_asset_class,
            benchmark_strategy,
            benchmark_asset,
            asset_to_class,
            registries,
            thresholds,
            horizon,
            half_life,
            n_min,
            theta,
            refuted_min,
            refuted_score,
            verdict_confidence,
        )
        results.append(result)
    return results


async def check_contradictions(db: InvestmentDB) -> list[ContradictionPair]:
    """docs/ARCHITECTURE.md 'Invariant contradiction check' — pairwise over
    `status='integrated'` invariants. Surfaced for the seed inventory / owner
    review (digest, M9+); does not auto-resolve anything."""
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
