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
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # TYPE-ONLY, and deliberately so: `snapshots.build_snapshot` must call
    # `effective_caps` + `drawdown_ok` below to stamp each ranked row with the
    # user-drawdown exclusion (CLAUDE.md "Ranking rule"), and a runtime import
    # here would make that a cycle (gates -> snapshots -> gates). Only
    # `switch_gates` needs the type, and only as an annotation.
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


def weights_well_formed(allocation: Mapping[str, float]) -> bool:
    """Every weight is a FINITE, NON-NEGATIVE number. An EMPTY map is
    well-formed — it means nothing is held, which is the opening state of the
    market-signal stack, not a malformed book.

    Split out of `allocation_well_formed` for the INCUMBENT side of the
    two-argument gates. Found by the property test, not by review: guarding only
    the target left `max_allocation_change_pts` and `turnover_pct` reading a NaN
    out of `current`, and both are comparisons, so both went blind exactly as
    they did for the target. A malformed incumbent is a different defect from a
    malformed target — DB or held state rather than a bad LLM answer — so the
    two get their own gate names."""
    return all(math.isfinite(w) and w >= 0.0 for w in allocation.values())


def allocation_well_formed(allocation: Mapping[str, float]) -> bool:
    """A TARGET book: well-formed weights AND at least one sleeve.

    A PRECONDITION, not a merit gate, and it has to run before all of them
    because of how the others are written: every numeric gate in this module is
    a comparison, and every comparison against NaN is False. So a NaN weight
    walks straight through `abs(sum - 100) > tolerance`, through `max(...) >
    cap`, through `change < min` and through `turnover > max` — measured, not
    reasoned: a `{SPY: 50, TLT: 50, GLD: nan}` reallocation passed all five
    gates and would have been persisted. `max()` makes it worse than uniform:
    it discards NaN or returns it depending on where the key sits in the dict,
    so the SAME allocation could be refused by different gates, or by none.

    NON-NEGATIVE is a separate rule with a separate reason: V1 is long-only
    (docs/DATA_MODELS.md allocation: percent weights summing to 100). A short
    leg is arithmetically invisible to the other gates — `{SPY: 50, TLT: 50,
    GLD: 30, IEF: -30}` sums to 100 and no sleeve exceeds the cap — so nothing
    downstream would have caught a book the owner cannot hold.

    `+inf` alone WAS caught (by the sum gate), but only incidentally, and an
    accidental refusal is not a control: it reports the wrong gate name and
    stops working the moment the arithmetic changes."""
    return bool(allocation) and weights_well_formed(allocation)


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
    to safety (the drawdown control), not a conviction bet, so the market-signal
    path passes `exempt=HAVEN_EXEMPT` — the whole haven CHAIN, IEF and the cash
    fallback it goes to when IEF is itself below trend (owner, 2026-08-08; it
    named IEF alone until the cap froze the stack in its stale book on four 2022
    dates). Empty by default, so the seeded-portfolio callers
    (switch/reallocation gates) are unchanged and still bind every sleeve."""
    considered = [w for t, w in allocation.items() if t not in exempt]
    return not considered or max(considered) <= caps.max_single_asset_pct


def effective_caps(user_profile: Mapping[str, Any], portfolio: Mapping[str, Any] | None) -> Caps:
    """The BINDING caps for one portfolio: the STRICTER of the user_profile and
    the portfolio's own rule (CLAUDE.md "Binding caps": per-portfolio rules may
    only be stricter). Both drawdown limits are negative, so stricter is the
    LARGER (less-negative) — `max`; for the single-asset cap stricter is the
    SMALLER — `min`. A portfolio without its own rule inherits the user cap
    unchanged.

    Lives HERE, beside `Caps` and the two predicates that consume it, rather
    than in `writeback` where it was written: it is a pure function over two
    mappings and this module's contract is exactly that ("PURE module: no I/O,
    no DB"). Three callers now need it — the reallocation disposition, the
    market-signal disposition, and `snapshots.build_snapshot`'s exclusion flag —
    and the cap algebra has to be one implementation for the same reason the
    percent/fraction conversion lives in `drawdown_ok`."""
    single = float(user_profile["max_single_asset_pct"])
    drawdown = float(user_profile["max_drawdown_pct"])
    if portfolio is not None:
        p_single = portfolio.get("max_single_asset_pct")
        p_drawdown = portfolio.get("max_drawdown_rule")
        if p_single is not None:
            single = min(single, float(p_single))
        if p_drawdown is not None:
            drawdown = max(drawdown, float(p_drawdown))
    return Caps(max_single_asset_pct=single, max_drawdown_pct=drawdown)


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
    challenger: "RankedRow",
    defender: "RankedRow",
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


def _sortino_gap(challenger: "RankedRow", defender: "RankedRow") -> float | None:
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


def cited_invariant_eligible(
    status: str,
    weight_effective: float,
    total_confrontations: int,
    market_score: float,
    active: bool,
    *,
    weight_min: float,
    refuted_min: int,
    refuted_score: float,
) -> bool:
    """UC8-B gate 6 eligibility for ONE cited invariant (docs/USE_CASES.md UC8-B
    gate 6; docs/TASKS.md Phase 6). A reallocation may only lean on an invariant
    that is:
    - `status='integrated'` — belief is not enough, history is (ADR-006).
      SETTLED at M8 (2026-07-30, measured on the live DB — MILESTONES.md M8):
      integrated-only stays. Admitting high-weight 'proposed' was rejected
      because `weight_effective` is dominated by the author-tier FLOOR, not by
      evidence — it would take the citable set from 2 to 218/253, letting a real
      allocation move lean on the 209 curator notes that carry no measurable
      effect. The evidence-shaped variant (score >= theta and N >= N_min) is
      principled but adds 0 citable invariants today; revisit once the corpus
      has matured.
    - heavy enough (`weight_effective >= weight_min`);
    - NOT measurably refuted (`>= refuted_min` confrontations AND
      `market_score < refuted_score` → ineligible, floor or not — a floored
      weight must not smuggle a refuted invariant back in);
    - ACTIVE now (its condition holds today, or is 'always'). A dormant
      invariant describes a market that is not present, so it cannot back a
      move made today."""
    if status != "integrated":
        return False
    if weight_effective < weight_min:
        return False
    if total_confrontations >= refuted_min and market_score < refuted_score:
        return False
    return active


def _inadmissible(
    current: Mapping[str, float],
    proposed: Mapping[str, float],
    allowed_tickers: frozenset[str],
) -> str | None:
    """IS THIS A PROPOSAL AT ALL? The refused gate's name, or None to continue.

    Everything here is answerable without knowing the user's caps or thresholds
    — that is the test for belonging in this function rather than in
    `_without_merit`, and the reason a check cannot drift between them without
    someone deciding it should.

    The two well-formedness checks are deliberately NOT called "gate 0", a name
    ADR-011 already owns across five documents for the mechanical-sovereignty
    check in `writeback.dispose_reallocation`. They assert the two arguments are
    books at all, because the merit gates are comparisons and a NaN or a short
    leg is invisible to every one of them. BOTH sides are guarded — the merit
    gates measure the target AGAINST the incumbent, so a NaN in `current` blinds
    them just as thoroughly — under separate names, because they are separate
    defects (held state vs proposed answer).

    UC8-B gate 5 (`allowed_tickers`) is here despite its number: a ticker the
    system has never heard of makes the proposal inadmissible, not merely
    unattractive."""
    if not allocation_well_formed(proposed):
        return "allocation_well_formed"
    if not weights_well_formed(current):
        return "current_allocation_well_formed"
    if set(proposed) - allowed_tickers:
        return "allowed_tickers"
    return None


def _without_merit(
    current: Mapping[str, float],
    proposed: Mapping[str, float],
    caps: Caps,
    thresholds: ProposalThresholds,
    exempt: frozenset[str] = frozenset(),
) -> str | None:
    """IS IT ANY GOOD? The refused gate's name, or None if it passes.

    UC8-B gates 1-4, in the spec's own order — every one of them a judgment
    against a cap or a threshold, which is exactly what `_inadmissible` has
    none of. Gate 3 is a FLOOR on the largest move, not a ceiling: a
    reallocation too small to matter is noise that only pays costs."""
    if abs(sum(proposed.values()) - 100.0) > ALLOCATION_SUM_TOLERANCE:
        return "allocation_sums_to_100"
    if not concentration_ok(proposed, caps, exempt=exempt):
        return "max_single_asset_pct"
    if max_allocation_change_pts(current, proposed) < thresholds.min_allocation_change_pts:
        return "min_allocation_change_pts"
    if turnover_pct(current, proposed) > thresholds.max_turnover_pct:
        return "max_turnover_pct"
    return None


def reallocation_gates(
    current: Mapping[str, float],
    proposed: Mapping[str, float],
    caps: Caps,
    thresholds: ProposalThresholds,
    allowed_tickers: frozenset[str],
    exempt: frozenset[str] = frozenset(),
) -> GateOutcome:
    """docs/USE_CASES.md UC8-B gates 1-5. Gate 6 (cited-invariant eligibility)
    is not here — see the module docstring: the mechanical replay cites
    nothing, so it has no input before M8.

    EVALUATION ORDER IS NOT THE SPEC'S NUMBERING, and it is not meant to be.
    Only the FIRST refusal is reported, so the order decides which reason the
    owner reads. The two helpers below make that order a CONSEQUENCE of what
    each check is rather than of where someone put its line: admissibility
    first, merit second, and a check moves only by being moved between two
    functions whose names say what they mean. Run in the spec's numbering, gate
    5 sat among the merit gates and a book naming an instrument that does not
    exist came back as `max_turnover_pct` — sending the owner after a book that
    moved too much instead of one built on a ticker the system has never heard
    of (owner decision 2026-08-06).

    `exempt` NAMES THE SLEEVES THE CONCENTRATION CAP DOES NOT BIND, and callers
    on the cognitive path now pass the haven chain (owner, 2026-08-09). Empty by
    default, so nothing changes for a caller that does not ask.

    Why the cognitive path needed it too. Measured at 2008-10-01 of the
    on-stack run: the incumbent was the stack's own IEF 100 — legal, because the
    market-signal path has exempted the haven since ADR-007's second addendum —
    and the Worker proposed IEF 72.5 / TLT 12.5 / GLD 10 / cash 5. A
    DE-concentration, refused by this cap, which left the 100% standing. The
    gate could only freeze the breach it was refusing to reduce.

    That is ADR-009's argument arriving in a third place, and the exemption is
    about the SLEEVE, not the path: a haven concentration is a safety redirect
    rather than a conviction bet, whoever proposes it. It does loosen a binding
    cap on every cognitive proposal, including the bridge defender's — stated
    plainly in docs/DECISIONS.md rather than hidden in the narrow case that
    prompted it."""
    reason = _inadmissible(current, proposed, allowed_tickers) or _without_merit(
        current, proposed, caps, thresholds, exempt
    )
    return GateOutcome.refused(reason) if reason else PASSED
