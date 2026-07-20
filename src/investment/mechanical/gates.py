"""Deterministic proposal gates (docs/USE_CASES.md UC8-A switch / UC8-B
reallocation) + the reallocation delta blend (docs/ARCHITECTURE.md "Proposal/
Adaptation delta blending") — "the Worker proposes, Writeback disposes"
(CLAUDE.md): every rule here is mechanical and decides without an LLM.

WHY these live in `mechanical/` and not in `writeback/`, which is their
eventual live caller: TWO callers must run the IDENTICAL rules — the Phase 9
replay (M6, this milestone) and Writeback (M8). docs/TASKS.md Task 9.4 pins
that they may not be two implementations: the replay "drives the SAME live
weekly chain over history, never a reimplemented decision loop (so replay
logic cannot DRIFT from live logic — the classic replay bug)". M8's Writeback
imports these functions; it does not restate them.

SCOPE (CLAUDE.md "no speculative stubs"): only the gates the M6 mechanical
replay exercises are implemented. The anti-repetition cooldown pre-gate
(UC8-A, `proposal_cooldown_weeks`) is deliberately absent — it keys off a
USER rejection, and the replay's agent-follow arm accepts every gated
proposal, so there is no rejection to cool down from. It arrives with the
user-decision path at M8. Likewise UC8-B gate 6 (cited-invariant
eligibility): the mechanical replay cites no invariants — it is "blind to
invariant weights" by design (docs/ARCHITECTURE.md) — so the gate has no
input until the Worker exists at M8.

PURE module: no I/O, no DB. Every threshold arrives as an argument.
"""

import dataclasses
from collections.abc import Mapping

from investment.mechanical.snapshots import RankedRow

# UC8-B gate 1: `proposed_allocation` sums to 100 (+-0.1).
ALLOCATION_SUM_TOLERANCE = 0.1
# docs/ARCHITECTURE.md "Proposal/Adaptation delta blending": the blended delta
# is "rounded to 2.5-point increments, then re-normalized to sum 100".
BLEND_ROUNDING_PTS = 2.5


@dataclasses.dataclass(frozen=True)
class Caps:
    """The BINDING user_profile caps (CLAUDE.md "Binding caps"): they bind the
    defender role AND all proposal candidacy; per-portfolio rules may only be
    STRICTER. Units are the schema's, unconverted (docs/DATA_MODELS.md "Units
    convention"): `_pct` fields are percent points on 0-100, while a
    portfolio's own `max_drawdown` indicator is a decimal fraction — the
    conversion is `drawdown_ok`'s job, and doing it there rather than at the
    call sites is why the two can never drift apart by a factor of 100."""

    max_single_asset_pct: float
    max_drawdown_pct: float


@dataclasses.dataclass(frozen=True)
class ProposalThresholds:
    """The system_thresholds this module reads (docs/DATA_MODELS.md
    system_thresholds). Named, not a bare dict, so the Phase 9 grid search
    cannot silently misspell a knob it is calibrating (docs/TASKS.md Task
    9.2)."""

    sortino_gap_min: float
    calmar_min: float
    min_allocation_change_pts: float
    max_turnover_pct: float
    # docs/ARCHITECTURE.md pins 0.4/0.6; Task 9.2 calibrates it, and M6's DoV
    # reads the result against I-35 (FAVORS' per-regime ranking is noise in 4
    # of 5 regimes, so a HIGH stable favors weight is suspicious, not
    # confirmation).
    blend_scenario_weight: float
    blend_favors_weight: float


@dataclasses.dataclass(frozen=True)
class GateOutcome:
    """`failed_gate` names the FIRST refusing gate — the digest (M8) and the
    replay's report both need to say WHY, not just "no"."""

    passed: bool
    failed_gate: str | None = None

    @classmethod
    def refused(cls, gate: str) -> "GateOutcome":
        return cls(passed=False, failed_gate=gate)


PASSED = GateOutcome(passed=True)


# -- shared cap checks (bind BOTH proposal kinds — CLAUDE.md "Binding caps") --


def concentration_ok(
    allocation: Mapping[str, float], caps: Caps, exempt: frozenset[str] = frozenset()
) -> bool:
    """No single sleeve above `max_single_asset_pct`.

    Judgment call (spec silent): the cap is applied to EVERY key including the
    synthetic 'cash' sleeve, uniformly — the literal reading of "single asset",
    and the strict direction, which is the safe one for a BINDING cap. No
    seeded portfolio or scenario target is affected (the largest cash sleeve
    is barbell-defensive's 30, under the cap — 50 since the ADR-007 addendum,
    40 when this was written).

    `exempt` names sleeves the cap does NOT bind — the ADR-007 addendum's
    trend-haven exception (docs/V1_STRATEGY.md, docs/DECISIONS.md). The market-signal
    overlay redirects the SPY/GLD sleeves into IEF below trend, which can
    concentrate the HAVEN to ~90% during risk-off; that is the deliberate flight
    to safety (the drawdown control), not a conviction bet, so the market-signal path
    passes `exempt={IEF}`. Empty by default, so the seeded-portfolio callers
    (switch/reallocation gates) are unchanged and still bind every sleeve."""
    considered = [w for t, w in allocation.items() if t not in exempt]
    return not considered or max(considered) <= caps.max_single_asset_pct


def drawdown_ok(max_drawdown: float | None, caps: Caps) -> bool:
    """The user drawdown rule. Breaching it "keeps the row ranked but excludes
    it from defender role and proposal candidacy" (CLAUDE.md "Ranking rule").

    Unit conversion lives here (see `Caps`): `max_drawdown` is a decimal
    fraction (-0.062 = -6.2%), `max_drawdown_pct` is percent points
    (-15.0 = -15%). Both are negative, so the test is `>=`: a drawdown of
    -0.20 is WORSE than the -0.15 rule.

    A missing indicator does not breach: it is unmeasured, not bad. It cannot
    smuggle a breach through either — an unranked portfolio loses on the
    ranking key long before it reaches this gate (`snapshots._indicator`)."""
    if max_drawdown is None:
        return True
    return max_drawdown >= caps.max_drawdown_pct / 100.0


# -- UC8-A: switch proposal ------------------------------------------------


def max_allocation_change_pts(current: Mapping[str, float], proposed: Mapping[str, float]) -> float:
    """The largest per-asset move, in allocation percent points, over the UNION
    of both sleeves — a ticker dropped to zero (absent from one map) is a full
    change of its own weight, not a missing key."""
    tickers = set(current) | set(proposed)
    if not tickers:
        return 0.0
    return max(abs(proposed.get(t, 0.0) - current.get(t, 0.0)) for t in tickers)


def switch_gates(
    challenger: RankedRow,
    defender: RankedRow,
    caps: Caps,
    thresholds: ProposalThresholds,
) -> GateOutcome:
    """docs/USE_CASES.md UC8-A, in the spec's own order:
    1. challenger outranks the defender in the snapshot;
    2. `sortino_rolling` gap >= `proposal_sortino_gap_min`;
    3. challenger `calmar_rolling` >= `proposal_calmar_min` — an ABSOLUTE
       floor, "compared to the threshold, not to the defender's Calmar": a
       challenger may pass with a WORSE Calmar or drawdown than the defender
       (the digest flags the weaker downside profile, EXAMPLE.md Step 8B);
    4. binding concentration + drawdown caps pass;
    5. at least one asset differs by >= `proposal_min_allocation_change_pts`.

    Gate 4's drawdown leg is what "excludes it from proposal candidacy"
    (CLAUDE.md) means concretely."""
    if challenger.rank >= defender.rank:
        return GateOutcome.refused("outranks_defender")

    gap = _sortino_gap(challenger, defender)
    if gap is None or gap < thresholds.sortino_gap_min:
        return GateOutcome.refused("sortino_gap_min")

    calmar = challenger.row.calmar_rolling
    if calmar is None or calmar < thresholds.calmar_min:
        return GateOutcome.refused("calmar_min")

    if not concentration_ok(challenger.row.allocation, caps):
        return GateOutcome.refused("max_single_asset_pct")
    if not drawdown_ok(challenger.row.max_drawdown, caps):
        return GateOutcome.refused("max_drawdown_pct")

    change = max_allocation_change_pts(defender.row.allocation, challenger.row.allocation)
    if change < thresholds.min_allocation_change_pts:
        return GateOutcome.refused("min_allocation_change_pts")

    return PASSED


def _sortino_gap(challenger: RankedRow, defender: RankedRow) -> float | None:
    """Read off `gap_to_defender` when the ranker computed it (challenger rows
    always carry it), so the gate cannot disagree with the snapshot the digest
    renders."""
    if challenger.gap_to_defender is not None:
        return challenger.gap_to_defender.get("sortino_rolling")
    a, b = challenger.row.sortino_rolling, defender.row.sortino_rolling
    return None if a is None or b is None else a - b


# -- UC8-B: reallocation proposal ------------------------------------------


def _delta(target: Mapping[str, float], current: Mapping[str, float]) -> dict[str, float]:
    return {t: target.get(t, 0.0) - current.get(t, 0.0) for t in set(target) | set(current)}


def blend_allocation(
    current: Mapping[str, float],
    scenario_target: Mapping[str, float] | None,
    favors_target: Mapping[str, float] | None,
    thresholds: ProposalThresholds,
) -> dict[str, float]:
    """docs/ARCHITECTURE.md "Proposal/Adaptation delta blending":
    `delta = 0.4 x scenario_delta + 0.6 x favors_delta`, rounded to 2.5-point
    increments, then re-normalized to sum 100.

    - scenario_delta = active scenario's `target_allocation` - current
      (tactical short-term override);
    - favors_delta = top-FAVORS strategy's PRESCRIBED allocation - current
      (structural anchor). "Prescribed allocation of a strategy = its
      base-scenario `target_allocation`".

    A `None` leg contributes a ZERO delta rather than voiding the blend —
    docs/EXAMPLE.md Step 8 does exactly this when the top-FAVORS strategy for
    the regime is already the defender's own ("delta = 0.4 x scenario_delta +
    0.6 x 0"). Weights are arguments, not constants, because Task 9.2
    calibrates them.

    Renormalization is multiplicative on the ROUNDED weights, so it can
    reintroduce sub-2.5 fractions; that is the pinned order ("rounded ...,
    then re-normalized"), and gate 1 only asks the sum to be 100."""
    scenario_delta = _delta(scenario_target, current) if scenario_target else {}
    favors_delta = _delta(favors_target, current) if favors_target else {}

    blended: dict[str, float] = {}
    for ticker in set(current) | set(scenario_delta) | set(favors_delta):
        delta = thresholds.blend_scenario_weight * scenario_delta.get(
            ticker, 0.0
        ) + thresholds.blend_favors_weight * favors_delta.get(ticker, 0.0)
        weight = current.get(ticker, 0.0) + delta
        # Negative weights are not shortable sleeves — V1 is long-only
        # (docs/DATA_MODELS.md allocation: percent weights summing to 100).
        blended[ticker] = max(0.0, _round_to(weight, BLEND_ROUNDING_PTS))

    total = sum(blended.values())
    if total <= 0:
        return dict(current)
    return {t: w / total * 100.0 for t, w in blended.items() if w > 0}


def _round_to(value: float, increment: float) -> float:
    return round(value / increment) * increment


def turnover_pct(current: Mapping[str, float], proposed: Mapping[str, float]) -> float:
    """`sum(|delta|)/2` in allocation percent points (UC8-B gate 4). The halving
    is what makes a FULL switch read as 100% turnover rather than 200%: every
    point sold is a point bought, and turnover counts the round trip once.

    NOTE the replay's COST model does NOT reuse this (docs/TASKS.md Task 9.1
    step 4): cost is `sum(|delta|) x replay_cost_bps` — the un-halved sum,
    "= 2 x turnover; do NOT also x2", because the bps are charged per SIDE."""
    tickers = set(current) | set(proposed)
    return sum(abs(proposed.get(t, 0.0) - current.get(t, 0.0)) for t in tickers) / 2.0


def reallocation_gates(
    current: Mapping[str, float],
    proposed: Mapping[str, float],
    caps: Caps,
    thresholds: ProposalThresholds,
    allowed_tickers: frozenset[str],
) -> GateOutcome:
    """docs/USE_CASES.md UC8-B gates 1-5, in the spec's own order. Gates 6
    (cited-invariant eligibility) is not here — see the module docstring: the
    mechanical replay cites nothing, so it has no input before M8.

    Gate 3 is a FLOOR on the largest move, not a ceiling: a reallocation too
    small to matter is noise that only pays costs."""
    if abs(sum(proposed.values()) - 100.0) > ALLOCATION_SUM_TOLERANCE:
        return GateOutcome.refused("allocation_sums_to_100")
    if not concentration_ok(proposed, caps):
        return GateOutcome.refused("max_single_asset_pct")
    if max_allocation_change_pts(current, proposed) < thresholds.min_allocation_change_pts:
        return GateOutcome.refused("min_allocation_change_pts")
    if turnover_pct(current, proposed) > thresholds.max_turnover_pct:
        return GateOutcome.refused("max_turnover_pct")
    unknown = set(proposed) - allowed_tickers
    if unknown:
        return GateOutcome.refused("allowed_tickers")
    return PASSED
