"""Writeback — the pure executor + mechanical proposal gates (docs/TASKS.md
Phase 6; docs/USE_CASES.md UC8). "Worker proposes, Writeback disposes"
(CLAUDE.md): the Worker's reallocation is validated by DETERMINISTIC gates here
and only then persisted, EventLog-first (CLAUDE.md "EventLog" rule).

Two responsibilities: the reallocation DISPOSITION (the cooldown pre-gate, then
gates 1-6, then commit the Proposal EventLog-first) and the knowledge COMMIT
(`commit_knowledge`) — the guardrailed PostPlannerResult persisted to the graph:
source='evaluation' confrontations (weight-moving, condition-gated), conviction
nudges, coherent scenario-probability updates, and innovations (dedup +
maturation). gate 6 (cited-invariant eligibility) and `effective_caps`
(stricter-of user/portfolio caps) are the two gates the mechanical replay could
not supply before M8 (mechanical/gates.py docstring).

A THIRD disposition, `dispose_market_signal`, carries ADR-007's live monthly
allocation decision: the mechanical stack proposes (mechanical/market_signal.py
`walk_decisions`), the binding caps dispose, and the decision is journalled
whether or not it moves money.

THE TWO DISPOSITIONS DO NOT OVERLAP, and gate 0 of `dispose_reallocation` is
what makes that structural rather than incidental (docs/DECISIONS.md ADR-011):
the mechanical allocation is SOVEREIGN, so a cognitive reallocation aimed at a
`TIME_VARYING_PORTFOLIOS` row is refused before any merit gate runs. The Worker
reads the mechanical decision, challenges it in prose, and routes a real
disagreement through `innovations_proposed` — the audited channel that already
matures mechanically under ADR-006 — never through the allocation itself.

The UC8-A SWITCH disposition is deliberately absent: ADR-007 superseded the
ranked defender/challenger duel, so no live cycle emits a switch. `switch_gates`
(mechanical/gates.py) stays, because the retained-bridge replay still runs it.
"""

import dataclasses
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ulid import ULID

from investment.corpus.embedding import Embedder, invariant_embedding_input, to_blob
from investment.db.seed_data import TIME_VARYING_PORTFOLIOS
from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios
from investment.mechanical.gates import (
    ALLOCATION_SUM_TOLERANCE,
    Caps,
    GateOutcome,
    ProposalThresholds,
    allocation_well_formed,
    cited_invariant_eligible,
    concentration_ok,
    effective_caps,
    max_allocation_change_pts,
    reallocation_gates,
    weights_well_formed,
)
from investment.mechanical.invariants import compute_weight_update, mature_seed_invariants
from investment.mechanical.market_signal import (
    BOOK_PORTFOLIO_IDS,
    CONFIRM_DECISIONS,
    STACK_PORTFOLIO_ID,
    TREND_HAVEN,
    Decision,
)
from investment.planner.context import active_invariant_ids
from investment.planner.post import PostPlannerResult
from investment.worker.result import ImprovementProposal, ReallocationProposal
from investment.writeback.knowledge import (
    DEDUP_COSINE_THRESHOLD,
    InvariantCorpus,
    author_band,
    find_duplicate,
    load_invariant_corpus,
)

logger = logging.getLogger(__name__)

PROPOSAL_EVENT = "ProposalEvent"
# ADR-007's live monthly allocation decision, journalled on EVERY decision date
# — including the ones that change nothing. A decision that lands on the held
# book still advanced the hysteresis counter and still read the 200d overlay;
# without that row the record would show only the months the stack moved, which
# is precisely the evidence forward paper-mode is NOT allowed to lose.
MARKET_SIGNAL_EVENT = "MarketSignalDecisionEvent"
CONFRONTATION_EVENT = "ConfrontationEvent"
EVALUATION_EVENT = "EvaluationEvent"
SCENARIO_EVENT = "ScenarioEvent"
INNOVATION_EVENT = "InnovationEvent"
SOURCE_UC = "UC8"
_SCENARIO_KINDS = frozenset({"bull", "base", "bear"})
_SCENARIO_SUM_TOLERANCE = 0.1
_STRATEGY_INNOVATION_TYPES = frozenset({"new_strategy", "strategy_revision"})
# Fallbacks for a strategy innovation whose spec omits (or invents) them:
# '4seasons' is V1's primary framework (db/seed_data.py), and a mid conviction
# states "unproven" — the probation verdict, not the spec, decides its fate.
DEFAULT_FRAMEWORK = "4seasons"
DEFAULT_CONVICTION = 50.0


def proposal_thresholds(thresholds: dict[str, float]) -> ProposalThresholds:
    """Read the named proposal knobs out of the system_thresholds map."""
    return ProposalThresholds(
        sortino_gap_min=thresholds["proposal_sortino_gap_min"],
        calmar_min=thresholds["proposal_calmar_min"],
        min_allocation_change_pts=thresholds["proposal_min_allocation_change_pts"],
        max_turnover_pct=thresholds["proposal_max_turnover_pct"],
        blend_scenario_weight=thresholds["blend_scenario_weight"],
        blend_favors_weight=thresholds["blend_favors_weight"],
    )


async def portfolio_caps(db: InvestmentDB, portfolio_id: str) -> dict[str, Any] | None:
    """A portfolio's OWN cap rules (CLAUDE.md "Binding caps": per-portfolio rules
    may only be STRICTER; `effective_caps` takes the stricter of these and the
    user profile). `None` when the portfolio is unknown — then only the user
    caps bind."""
    rows = await db.query(
        "SELECT max_single_asset_pct, max_drawdown_rule FROM portfolio WHERE id = :pid",
        pid=portfolio_id,
    )
    return dict(rows[0]) if rows else None


async def _allowed_reallocation_tickers(db: InvestmentDB) -> frozenset[str]:
    """The tickers a reallocation may hold (docs/TASKS.md Phase 6 gate B): active
    tradable tickers (non-MACRO asset class) plus the synthetic 'cash' sleeve."""
    rows = await db.query(
        "SELECT ticker FROM allowed_tickers WHERE active = 1 AND asset_class != 'MACRO'"
    )
    return frozenset({str(r["ticker"]) for r in rows} | {"cash"})


async def cooldown_pre_gate(
    db: InvestmentDB,
    defender_id: str,
    proposed: dict[str, float],
    thresholds: dict[str, float],
    regime_type: str | None,
    today: date,
) -> GateOutcome:
    """The anti-repetition pre-gate (CLAUDE.md "UC8 — Worker proposes, Writeback
    disposes": "a 4-week anti-repetition cooldown").

    ADR-006 REINTERPRETATION, stated explicitly because it departs from the
    letter of docs/USE_CASES.md UC8-A: the spec keys the cooldown on "a
    challenger rejected BY THE USER within the last proposal_cooldown_weeks",
    and ADR-006 deleted user rejections entirely — so the trigger as written can
    never fire. What survives is the PURPOSE (don't re-litigate the same idea
    every cycle) and the escape clause ("unless the regime type has changed"),
    both kept here, re-keyed onto what V1 actually has: a recently COMMITTED
    proposal for the same defender.

    Refused iff a proposal for this defender inside the window proposed a
    NEAR-IDENTICAL allocation — near-identical meaning no asset differs by
    `proposal_min_allocation_change_pts`, the same test gate 3 uses against the
    current book, so the cooldown introduces no new knob. A genuinely different
    tilt passes: the UC9 ad-hoc re-run (one per day) exists to act on new
    information, and blocking it would cost signal. The regime escape lifts the
    cooldown when the world changed under the old proposal.

    Blocked proposals never reach the ledger, so only proposals actually EMITTED
    can start a cooldown — nothing was shown to the owner, so there is nothing
    to not-repeat."""
    window_weeks = thresholds["proposal_cooldown_weeks"]
    since = (today - timedelta(weeks=int(window_weeks))).isoformat()
    rows = await db.query(
        "SELECT proposed_allocation, market_context FROM proposal "
        "WHERE defender_id = :pid AND proposed_allocation IS NOT NULL AND date >= :since "
        "ORDER BY date DESC",
        pid=defender_id,
        since=since,
    )
    min_change = thresholds["proposal_min_allocation_change_pts"]
    for row in rows:
        previous = json.loads(str(row["proposed_allocation"]))
        if max_allocation_change_pts(previous, proposed) >= min_change:
            continue  # a materially different tilt — not a repeat
        context = json.loads(str(row["market_context"])) if row["market_context"] else {}
        if regime_type is not None and context.get("regime") != regime_type:
            continue  # the regime type changed since: the escape clause lifts it
        return GateOutcome.refused("cooldown_repeat_proposal")
    return GateOutcome(passed=True)


# The Worker's own book (decision_cycle.WORKER_BOOK_ID). Declared here rather
# than imported: writeback is imported BY decision_cycle, and a back-import
# would close a cycle. A mismatch is caught by
# `test_the_cognitive_book_id_agrees_across_the_two_modules`.
COGNITIVE_BOOK_ID = "worker-book"


async def gate6_cited_invariants(
    db: InvestmentDB,
    invariant_ids: list[str],
    thresholds: dict[str, float],
    regime_type: str | None,
) -> GateOutcome:
    """UC8-B gate 6 over the whole cited set. A reallocation must CITE support
    and every cited invariant must be eligible (mechanical/gates.py
    `cited_invariant_eligible`): an uncited reallocation, or one citing an
    ineligible invariant, is refused. Active-now is computed once via
    `active_invariant_ids` (context.py), the rest read per invariant."""
    if not invariant_ids:
        if not await _any_citable(db, thresholds, regime_type):
            return GateOutcome.refused("gate6_corpus_has_nothing_citable")
        return GateOutcome.refused("gate6_no_cited_invariant")

    placeholders = ",".join(f":i{n}" for n in range(len(invariant_ids)))
    params = {f"i{n}": iid for n, iid in enumerate(invariant_ids)}
    rows = await db.query(
        "SELECT id, status, weight_effective, confirmation_count, infirmation_count, market_score "
        f"FROM invariant WHERE id IN ({placeholders})",
        **params,
    )
    by_id = {str(r["id"]): r for r in rows}
    active = await active_invariant_ids(db, invariant_ids, regime_type)

    for iid in invariant_ids:
        row = by_id.get(iid)
        if row is None:
            return GateOutcome.refused("gate6_unknown_invariant")
        eligible = cited_invariant_eligible(
            status=str(row["status"]),
            weight_effective=float(row["weight_effective"]),
            total_confrontations=int(row["confirmation_count"]) + int(row["infirmation_count"]),
            market_score=float(row["market_score"]),
            active=iid in active,
            weight_min=thresholds["proposal_invariant_weight_min"],
            refuted_min=int(thresholds["invariant_refuted_min_confrontations"]),
            refuted_score=thresholds["invariant_refuted_score"],
        )
        if not eligible:
            # TWO DIFFERENT FACTS UNDER ONE NAME, until now. "The Worker cited a
            # lighthouse it should not have" is an agent error; "nothing in the
            # corpus was citable this month" is a fact ABOUT THE CORPUS, and the
            # owner needs to tell them apart — the second is not a failure of
            # reasoning and no prompt change can fix it.
            #
            # Measured on the M8b covid episode: two of three proposals died
            # here, and both looked like the Worker misbehaving. It was not: the
            # integrated invariants are inflation-shaped and dormant in a
            # deflationary crisis, so the citable set was empty whatever it
            # chose.
            if not await _any_citable(db, thresholds, regime_type):
                return GateOutcome.refused("gate6_corpus_has_nothing_citable")
            return GateOutcome.refused("gate6_cited_invariant_eligibility")
    return GateOutcome(passed=True)


async def _any_citable(
    db: InvestmentDB, thresholds: dict[str, float], regime_type: str | None
) -> bool:
    """Whether ANY invariant would pass gate 6 right now.

    Asked only when a citation has already failed, so its cost is paid on the
    refusal path and never on the happy one. The candidate set is narrowed in
    SQL to `status='integrated'` — the only status gate 6 admits — which keeps
    the ACTIVE-now evaluation (one query per referenced signal) to a handful of
    rows rather than the whole table."""
    rows = await db.query(
        "SELECT id, status, weight_effective, confirmation_count, infirmation_count, "
        "market_score FROM invariant WHERE status = 'integrated'"
    )
    if not rows:
        return False
    active = await active_invariant_ids(db, [str(r["id"]) for r in rows], regime_type)
    return any(
        cited_invariant_eligible(
            status=str(r["status"]),
            weight_effective=float(r["weight_effective"]),
            total_confrontations=int(r["confirmation_count"]) + int(r["infirmation_count"]),
            market_score=float(r["market_score"]),
            active=str(r["id"]) in active,
            weight_min=thresholds["proposal_invariant_weight_min"],
            refuted_min=int(thresholds["invariant_refuted_min_confrontations"]),
            refuted_score=thresholds["invariant_refuted_score"],
        )
        for r in rows
    )


async def _commit_reallocation(
    db: InvestmentDB,
    defender_id: str,
    reallocation: ReallocationProposal,
    current: dict[str, float],
    market_context: dict[str, Any],
    today: date,
    citation_verdict: str | None = None,
) -> str:
    """Persist a passing reallocation: ProposalEvent to the EventLog FIRST, then
    the Proposal vertex, then upgrade the defender's latest snapshot
    recommendation — all in ONE transaction (CLAUDE.md "EventLog"). `outcome` is
    left NULL: evaluate_proposals() picks it up as pending at +12w.

    `paper_started` is set to `today` (ADR-006): the schema comment "set when
    user accepts a paper-test" predates the no-user-gate decision, and there is
    no accept step left — a proposal that passed every gate IS the paper-test,
    and paper_test_progress() / the digest scoreboard track it from this date. It
    is the same date as `date` today; they are kept distinct because they answer
    different questions (when it was proposed vs when tracking began)."""
    proposal_id = str(ULID())
    allocation_diff = {
        t: reallocation.proposed_allocation.get(t, 0.0) - current.get(t, 0.0)
        for t in set(reallocation.proposed_allocation) | set(current)
    }
    trace = (
        "UC8-B reallocation: passed gates 1-6 (caps, min-change, turnover, "
        "allowed-tickers, cited-invariant eligibility). ADR-008: rank/gap NULL "
        "on the market-signal path."
    )
    async with db.transaction():
        await db.append_event(
            type=PROPOSAL_EVENT,
            source_uc=SOURCE_UC,
            source_id=proposal_id,
            payload={
                "proposal_type": "reallocation",
                "defender_id": defender_id,
                "proposed_allocation": reallocation.proposed_allocation,
            },
            event_date=today,
        )
        await db.command(
            "INSERT INTO proposal (id, date, proposal_type, defender_id, proposed_allocation, "
            "recommendation, gap, market_context, reasoning, paper_started, citation_verdict, "
            "trace, created_at) "
            "VALUES (:id, :date, 'reallocation', :defender, :alloc, 'paper-test', :gap, :ctx, "
            ":reason, :date, :verdict, :trace, :now)",
            id=proposal_id,
            date=today.isoformat(),
            defender=defender_id,
            alloc=json.dumps(reallocation.proposed_allocation),
            gap=json.dumps({"allocation_diff": allocation_diff}),
            ctx=json.dumps(market_context),
            reason=reallocation.reasoning,
            verdict=citation_verdict,
            trace=trace,
            # created_at is a TIMESTAMP, not the proposal's business date
            # (CLAUDE.md: all persisted timestamps UTC).
            now=datetime.now(UTC).isoformat(),
        )
        # The cited invariants as a RELATION (proposal_cites) — so outcomes.py
        # can read the cited set back at +12w for the source='proposal'
        # confrontations. Gate 6 already proved every one exists and is
        # eligible, so these inserts cannot orphan.
        for invariant_id in reallocation.supporting_invariants:
            await db.command(
                "INSERT OR IGNORE INTO proposal_cites (proposal_id, invariant_id) "
                "VALUES (:pid, :iid)",
                pid=proposal_id,
                iid=invariant_id,
            )
        # Upgrade the target's latest snapshot recommendation so the digest
        # reflects that a paper-test was emitted (no-op if no snapshot yet).
        await db.command(
            "UPDATE portfolio_weekly_snapshot SET recommendation = 'paper-test' "
            "WHERE portfolio_id = :pid AND date = "
            "(SELECT MAX(date) FROM portfolio_weekly_snapshot WHERE portfolio_id = :pid)",
            pid=defender_id,
        )
        # THE BOOK ACTUALLY MOVES. Same treatment `dispose_market_signal` gives
        # `ms-stack`, and for the same reason: the `allocation` column records
        # what is HELD, so a passing proposal must move it or the row describes a
        # book nobody holds. Without this the cognitive book would have no NAV of
        # its own — every proposal would be recorded and none would ever change
        # anything, which is precisely the state that left the Worker judged on
        # five stitched-together 12-week windows.
        #
        # Inside the transaction, after the EventLog appends, and only on a
        # proposal that passed every gate. Still paper: V1 executes nothing
        # (ADR-006) and the owner alone places orders — this is what the book
        # WOULD have held, priced like every other paper series in the ranking.
        await db.command(
            "UPDATE portfolio SET allocation = :alloc, updated_at = :now WHERE id = :id",
            alloc=json.dumps(reallocation.proposed_allocation),
            now=datetime.now(UTC).isoformat(),
            id=defender_id,
        )
    return proposal_id


async def dispose_reallocation(
    db: InvestmentDB,
    reallocation: ReallocationProposal,
    defender_id: str,
    current_allocation: dict[str, float],
    user_profile: dict[str, Any],
    thresholds: dict[str, float],
    regime_type: str | None,
    market_context: dict[str, Any],
    *,
    portfolio: dict[str, Any] | None = None,
    today: date | None = None,
) -> tuple[GateOutcome, str | None]:
    """Run the anti-repetition pre-gate, the reallocation gates (UC8-B 1-5) and
    gate 6 and, on pass, commit the Proposal. Returns `(outcome, proposal_id)` —
    `proposal_id` is None on a block (no vertex; the caller sends the ⛔ Telegram
    note with the failed gate). "Worker proposes, Writeback disposes" — nothing
    is persisted until every gate passes.

    Order: the PURE gates run before the two DB-backed ones (cooldown, gate 6),
    so a malformed proposal costs no query. That ordering only decides WHICH gate
    name the digest reports when several would refuse — except gate 0, which
    runs FIRST because it is about jurisdiction, not merit: a proposal aimed at
    a mechanically-allocated portfolio must be refused for THAT reason, not for
    whichever cap it also happened to breach."""
    today = today or date.today()
    # GATE 0 — mechanical sovereignty (docs/DECISIONS.md ADR-011). A portfolio
    # whose allocation is produced by a mechanical decision path does not take
    # cognitive reallocations, full stop.
    #
    # WHY THIS IS A GATE AND NOT A PROMPT LINE. Until now the separation held by
    # accident: the Worker's reallocation is applied to whatever carries the
    # `defender` flag, and `ms-stack` happens to be seeded `defender: False`.
    # Nothing checked WHICH portfolio — the caps, turnover and citation gates
    # all look at the allocation and never at its owner. So a single flag flip
    # would have let the 0.4/0.6 blend overwrite the adopted allocation and
    # persist it as a `Proposal(reallocation)` with every gate green. That flip
    # is not hypothetical: retiring the bridge (docs/V1_STRATEGY.md Step 6) is
    # exactly the moment someone makes the stack the defender.
    if defender_id in TIME_VARYING_PORTFOLIOS:
        return GateOutcome.refused("mechanical_allocation"), None
    caps = effective_caps(user_profile, portfolio)
    thr = proposal_thresholds(thresholds)
    allowed = await _allowed_reallocation_tickers(db)

    outcome = reallocation_gates(
        current_allocation, reallocation.proposed_allocation, caps, thr, allowed
    )
    if not outcome.passed:
        return outcome, None

    cooldown = await cooldown_pre_gate(
        db, defender_id, reallocation.proposed_allocation, thresholds, regime_type, today
    )
    if not cooldown.passed:
        return cooldown, None

    # GATE 6 RECORDS INSTEAD OF REFUSING, on the cognitive book (owner decision
    # 2026-08-08: validate DOWNSTREAM, not upstream).
    #
    # Its principle was sound — a reallocation should lean on a claim already
    # confronted over 35 years — and its arithmetic was not: 9 invariants of 673
    # have reached `integrated`, so on most months nothing is citable whatever
    # the Worker chooses. Across three M8b runs it refused five times and, in the
    # 2008 episode, left the cognitive layer entirely mute.
    #
    # What made it unanswerable is that a refusal returned `None` — no proposal
    # row, so no `paper_started`, so `outcomes.evaluate_proposals` never scored
    # it at +12w. The gate destroyed the evidence that could contradict it. Now
    # the verdict is WRITTEN and the proposal lives, so in twelve weeks the
    # question has an answer: did the ones it would have blocked do worse?
    #
    # STILL BINDING ELSEWHERE. Only the cognitive book (`worker-book`) is a
    # shadow the owner never holds — nothing is at risk there, so refusing
    # protects nothing and only prevents learning (the ADR-009 argument: telling
    # beats refusing when refusing cannot exit a position). On any other target
    # a reallocation is a paper-test the owner may act on, and the gate binds.
    gate6 = await gate6_cited_invariants(
        db, reallocation.supporting_invariants, thresholds, regime_type
    )
    verdict = "passed" if gate6.passed else str(gate6.failed_gate)
    if not gate6.passed and defender_id != COGNITIVE_BOOK_ID:
        return gate6, None

    proposal_id = await _commit_reallocation(
        db,
        defender_id,
        reallocation,
        current_allocation,
        market_context,
        today,
        citation_verdict=verdict,
    )
    return GateOutcome(passed=True), proposal_id


# -- ADR-007: the live monthly market-signal disposition --------------------


def market_signal_gates(
    target: dict[str, float],
    held: dict[str, float],
    caps: Caps,
    allowed_tickers: frozenset[str],
) -> GateOutcome:
    """REGRESSION GUARDS on ADR-007's live allocation decision — and the name is
    the honest one, because these cannot refuse a decision the MARKET produced.

    What they actually catch is a CONFIGURATION or CODE change: a ticker
    deactivated in `allowed_tickers`, a `BOOKS` weight edited to something that
    no longer sums to 100 or breaches the single-asset cap. Over the 12
    reachable book x overlay states, every one passes — measured, not assumed.
    That is the correct outcome (the books were designed against these caps), but
    it means the guards must not be read as a safety control on the allocation.
    They assert the code still agrees with ADR-007; they do not protect capital.

    1. the target sums to 100 (a malformed book is a bug, not an allocation);
    2. `max_single_asset_pct`, with TREND_HAVEN exempt — the overlay can pile
       both equity/gold sleeves into IEF (~90% in risk-off), the deliberate
       flight to safety the validated -23.8% includes (ADR-007 addendum, choice
       (a)). Same predicate and exemption as `market_signal.cap_violations`
       applies over the 35y backtest;
    3. every sleeve is an active tradable ticker.

    THE DRAWDOWN RULE IS NOT HERE, and that is the correction this function
    exists in its current shape to record (docs/DECISIONS.md ADR-009). Blocking
    a proposal cannot protect anything: a refusal writes no Proposal, so no
    order is put in front of the owner and the stack stays exactly where it is.
    It can only FREEZE a position, never exit one — and during a drawdown the
    proposal being blocked is precisely the 200d overlay's flight into IEF.
    Measured over the three historical episodes (2008/2020/2022), a -25%
    trigger would have fired on 2020-03-20, the exact bottom, selling into the
    trough and missing the 185-day recovery. The rule now surfaces as an ALERT
    on the stack's realized 36M drawdown (telegram/digest.py) and never blocks.

    ALSO NOT APPLIED: `max_turnover_pct` (30) and `min_allocation_change_pts`
    (5), knobs of the 0.4/0.6 reallocation BLEND that ADR-007 superseded. A book
    switch is a ~90-100% turnover move by construction — the books barely
    overlap — so that ceiling would block every switch the strategy exists to
    make. And gate 6 (cited-invariant eligibility) plus the 4-week cooldown: the
    book is chosen by a market-priced signal validated over 35 years, not argued
    from a lighthouse, so there is no citation to check; the cooldown would
    suppress the overlay's re-entry, which IS the drawdown control."""
    # Same well-formedness precondition as the reallocation path, for a
    # different threat: here the target is machine-built (BOOKS x overlay), so a
    # NaN or a negative weight means a code or config defect rather than a bad
    # LLM answer — which is exactly what this function exists to catch, and what
    # the comparisons below would silently pass.
    if not allocation_well_formed(target):
        return GateOutcome.refused("allocation_well_formed")
    # HELD is checked too, and it matters more here than the symmetry suggests.
    # `dispose_market_signal` decides whether to emit on
    # `max_allocation_change_pts(held, target) > 0`, so a NaN in the held book
    # makes that max NaN and the comparison False — a REAL book rotation would
    # emit no proposal and journal `moves: False`, i.e. the strategy's whole
    # output for that month lost, silently. Measured: 3 of 10 ticker spellings
    # trip it, because `max()` keeps or discards NaN by set-iteration order. An
    # empty held book is legal and NOT malformed — it is the opening entry
    # (`market_signal_cycle.held_allocation`), hence `weights_well_formed`.
    # Refusing is the right answer rather than treating it as "nothing held":
    # a refusal is LOUD in the digest (ADR-009), where a guessed incumbent would
    # quietly misprice the +12w verdict.
    if not weights_well_formed(held):
        return GateOutcome.refused("held_allocation_well_formed")
    if abs(sum(target.values()) - 100.0) > ALLOCATION_SUM_TOLERANCE:
        return GateOutcome.refused("allocation_sums_to_100")
    if not concentration_ok(target, caps, exempt=frozenset({TREND_HAVEN})):
        return GateOutcome.refused("max_single_asset_pct")
    unknown = set(target) - allowed_tickers
    if unknown:
        return GateOutcome.refused("allowed_tickers")
    return GateOutcome(passed=True)


async def dispose_market_signal(
    db: InvestmentDB,
    decision: Decision,
    held_allocation: dict[str, float],
    market_context: dict[str, Any],
    user_profile: dict[str, Any],
    *,
    today: date,
) -> tuple[GateOutcome, str | None]:
    """ADR-007's live monthly decision, disposed and persisted in ONE
    transaction: the decision journal entry always, the Proposal only when the
    target differs from what is HELD and every gate passes.

    Held-relative, not walk-relative: `Decision.changed` compares against the
    PREVIOUS decision in the walk, but what matters live is the gap to the book
    actually held. The two diverge whenever a gate blocked an earlier move — and
    then the walk says "no change" while the stack is still sitting in the wrong
    book. Keying the emit on `held_allocation` makes the path self-healing: the
    next monthly decision re-proposes what the blocked one could not.

    Returns `(outcome, proposal_id)`. `outcome.passed` answers "did the gates
    admit this decision", and NOTHING else: a month that legitimately does not
    move returns a PASSING outcome with `proposal_id` None. It used to return
    `refused("no_change")`, which contradicted this function's own comment —
    2.8 book changes a year means ~9 holding months, and reporting the strategy
    working as a gate refusal made the log and the result object lie about the
    only distinction that matters here. Callers separate the two cases on
    `proposal_id`."""
    book_id = BOOK_PORTFOLIO_IDS[decision.held]
    # The STRICTER of three, not two (CLAUDE.md "Binding caps": per-portfolio
    # rules may only be stricter). The object actually held is the STACK
    # (ADR-009), so its row binds; the book's row binds too because the target
    # IS that book, post-overlay. Identical today (both 50 / -25), but reading
    # only the book's meant tightening `ms-stack`'s own cap would have been
    # silently ignored by the live path.
    stack_caps = effective_caps(user_profile, await portfolio_caps(db, STACK_PORTFOLIO_ID))
    # `Caps`'s field names are exactly `effective_caps`'s user-profile shape, so
    # the result of one round feeds straight into the next.
    caps = effective_caps(dataclasses.asdict(stack_caps), await portfolio_caps(db, book_id))
    allowed = await _allowed_reallocation_tickers(db)
    outcome = market_signal_gates(decision.target, held_allocation, caps, allowed)

    # A decision that lands on what is already held is not a refusal — it is the
    # strategy working (2.8 book changes a year; most months hold). It is
    # journalled and no Proposal is emitted.
    moves = max_allocation_change_pts(held_allocation, decision.target) > 0.0
    emit = moves and outcome.passed
    proposal_id = str(ULID()) if emit else None

    async with db.transaction():
        await db.append_event(
            type=MARKET_SIGNAL_EVENT,
            source_uc=SOURCE_UC,
            source_id=proposal_id,
            payload={
                **market_context,
                "gate": "passed" if outcome.passed else outcome.failed_gate,
                "moves": moves,
                "proposal_id": proposal_id,
            },
            event_date=today,
        )
        if proposal_id is not None:
            await _insert_market_signal_proposal(
                db, proposal_id, book_id, decision, held_allocation, market_context, today
            )
            # The stack Portfolio's `allocation` records what is HELD, so it
            # moves only when a proposal is actually emitted — inside this
            # transaction, after the EventLog appends. A blocked or no-change
            # decision leaves it exactly where it was, which is the truth.
            await db.command(
                "UPDATE portfolio SET allocation = :alloc, updated_at = :now WHERE id = :id",
                alloc=json.dumps(decision.target),
                now=datetime.now(UTC).isoformat(),
                id=STACK_PORTFOLIO_ID,
            )
    return outcome, proposal_id


async def _insert_market_signal_proposal(
    db: InvestmentDB,
    proposal_id: str,
    book_id: str,
    decision: Decision,
    held_allocation: dict[str, float],
    market_context: dict[str, Any],
    today: date,
) -> None:
    """The Proposal vertex for a passing market-signal decision. Called inside
    `dispose_market_signal`'s transaction, AFTER its EventLog append (CLAUDE.md
    "EventLog": every UC side-effect is appended before its vertex commit).

    ADR-008 shapes the row: `defender_id` is the book now in force, and
    `challenger_id` / `defender_rank` / `challenger_rank` / `gap` are NULL —
    a market-signal proposal has a signal state and a book, not a rank and a
    duel. `paper_started = today` because a proposal that passed every gate IS
    the paper-test (ADR-006 left no accept step), exactly as the reallocation
    path sets it.

    No `proposal_cites` rows: the decision cites no invariant (see
    `market_signal_gates`). `outcomes.evaluate_proposals` reads the incumbent
    back out of `market_context.held_allocation` rather than from a weekly
    snapshot, because the held book is the POST-OVERLAY allocation and no
    snapshot carries it."""
    await db.append_event(
        type=PROPOSAL_EVENT,
        source_uc=SOURCE_UC,
        source_id=proposal_id,
        payload={
            "proposal_type": "market-signal",
            "defender_id": book_id,
            "proposed_allocation": decision.target,
            "signal_state": decision.signalled,
            "held_book": decision.held,
        },
        event_date=today,
    )
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, proposed_allocation, "
        "recommendation, market_context, reasoning, paper_started, trace, created_at) "
        "VALUES (:id, :date, 'market-signal', :book, :alloc, 'paper-test', :ctx, :reason, "
        ":date, :trace, :now)",
        id=proposal_id,
        date=today.isoformat(),
        book=book_id,
        alloc=json.dumps(decision.target),
        ctx=json.dumps(market_context),
        reason=_market_signal_reasoning(decision, held_allocation),
        trace=(
            "ADR-007 live monthly market-signal decision: "
            f"signal={decision.signalled}, held={decision.held}, "
            f"below-trend={list(decision.below_trend)}; passed the binding caps "
            "(max_single_asset_pct with the IEF trend-haven exemption, allowed "
            "tickers, stack max_drawdown_pct). ADR-008: rank/gap NULL."
        ),
        now=datetime.now(UTC).isoformat(),
    )


def _market_signal_reasoning(decision: Decision, held_allocation: dict[str, float]) -> str:
    """`Proposal.reasoning` for a mechanical decision. The schema wants prose;
    what the owner needs is WHY the book is what it is — the two signal
    comparisons, the hysteresis state, and which sleeves the overlay moved. No
    LLM wrote this and the text should not pretend one did."""
    spread_side = _side(decision.spread, decision.spread_median, "wide", "tight")
    slope_side = _side(decision.slope, decision.slope_median, "steep", "flat")
    parts = [
        f"Credit spread (BAA10Y) {decision.spread:.2f} is {spread_side} its 10y median "
        f"({_num(decision.spread_median)}); yield slope (T10Y2Y) {decision.slope:.2f} is "
        f"{slope_side} its 10y median ({_num(decision.slope_median)}) "
        f"-> signal '{decision.signalled}'.",
    ]
    if decision.pending is not None:
        parts.append(
            f"Hysteresis: '{decision.pending}' has been signalled {decision.pending_count} of the "
            f"{CONFIRM_DECISIONS} consecutive decisions it needs, so the stack still holds "
            f"'{decision.held}'."
        )
    below = decision.below_trend
    parts.append(
        f"200d overlay: {', '.join(below)} below trend -> redirected to {TREND_HAVEN}."
        if below
        else "200d overlay: no sleeve below trend, the book is held as designed."
    )
    parts.append(f"Held {held_allocation or '(nothing yet)'} -> target {decision.target}.")
    return " ".join(parts)


def _side(value: float, median: float | None, above: str, below: str) -> str:
    """Which side of its trailing median a signal sits on, in the strategy's own
    vocabulary. `median is None` is the warm-up state `classify_regime` answers
    with its credit-spread-wide default — say so rather than inventing a side."""
    if median is None:
        return "unmeasurable against (warm-up, under 10y of history)"
    return above if value > median else below


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


# -- knowledge commit (PostPlannerResult -> graph) --------------------------


@dataclasses.dataclass(frozen=True)
class KnowledgeCommit:
    """What the knowledge commit persisted this cycle — the counts the digest
    reports. `innovations` counts what was CREATED: a new_invariant merged into
    an existing one by the dedup gate is not a creation."""

    confrontations: int
    conviction_updates: int
    scenario_updates: int = 0
    innovations: int = 0


async def _commit_confrontations(
    db: InvestmentDB,
    post_result: PostPlannerResult,
    context_regime_type: str | None,
    active: set[str],
    thresholds: dict[str, float],
    today: date,
) -> int:
    """source='evaluation' confrontations (docs/ARCHITECTURE.md confrontation
    rule). CONDITION GATE: only invariants ACTIVE now are confronted — a
    dormant lighthouse describes a market not present, so crediting/blaming it
    would be noise. Each confrontation bumps the count and recomputes the
    weight through the SHARED primitive (`compute_weight_update`) every
    confrontation source funnels into; `days_since=0` because the condition is
    active right now."""
    confrontations = [c for c in post_result.confrontations if c.invariant_id in active]
    if not confrontations:
        return 0

    ids = [c.invariant_id for c in confrontations]
    placeholders = ",".join(f":i{n}" for n in range(len(ids)))
    params = {f"i{n}": iid for n, iid in enumerate(ids)}
    rows = await db.query(
        "SELECT id, weight_initial, floor_weight, confirmation_count, infirmation_count "
        f"FROM invariant WHERE id IN ({placeholders})",
        **params,
    )
    inv = {str(r["id"]): r for r in rows}
    half_life = thresholds["recency_half_life_days"]
    descriptor = f"evaluation:{context_regime_type}"
    now = datetime.now(UTC).isoformat()

    committed = 0
    async with db.transaction():
        await db.append_event(
            type=CONFRONTATION_EVENT,
            source_uc=SOURCE_UC,
            source_id=None,
            payload={"source": "evaluation", "count": len(confrontations)},
            event_date=today,
        )
        for cf in confrontations:
            row = inv.get(cf.invariant_id)
            if row is None:
                continue
            cc = int(row["confirmation_count"]) + (1 if cf.verdict == "confirmed" else 0)
            ic = int(row["infirmation_count"]) + (1 if cf.verdict == "refuted" else 0)
            score, recency, w_eff = compute_weight_update(
                float(row["weight_initial"]), float(row["floor_weight"]), cc, ic, 0, half_life
            )
            await db.command(
                "INSERT INTO invariant_confrontations "
                "(id, invariant_id, moment_context, date, verdict, severity, source, source_id) "
                "VALUES (:id, :iid, :ctx, :date, :verdict, 1.0, 'evaluation', NULL)",
                id=str(ULID()),
                iid=cf.invariant_id,
                ctx=descriptor,
                date=today.isoformat(),
                verdict=cf.verdict,
            )
            await db.command(
                "UPDATE invariant SET confirmation_count = :cc, infirmation_count = :ic, "
                "market_score = :score, recency_factor = :recency, weight_effective = :weff, "
                "updated_at = :now WHERE id = :id",
                cc=cc,
                ic=ic,
                score=score,
                recency=recency,
                weff=w_eff,
                now=now,
                id=cf.invariant_id,
            )
            committed += 1
    return committed


async def _commit_evaluations(db: InvestmentDB, post_result: PostPlannerResult, today: date) -> int:
    """Persist each evaluation on the routing docs/DATA_MODELS.md "Persistence
    Routing" pins — `EventLog -> vertex (events[] filled) -> UPDATES` — and
    apply its conviction_delta to the strategy (clamped 0-100). The verdict
    itself matures MECHANICALLY at +12w (outcomes.py); this only nudges the
    Worker's running conviction, it does not adopt/reject anything (ADR-006).

    THE VERTEX IS THE POINT, not the conviction nudge. `conviction` is one
    float that the next evaluation overwrites, so without the row the observed
    events, the reasoning and the delta that moved it were all discarded the
    moment they were applied — a strategy's conviction could drift 40 points
    across a quarter with nothing on record saying why. Evaluation is one of
    the 13 entities and UPDATES one of the 10 relations; both were dead.

    The row is written for EVERY evaluation including `conviction_delta == 0`
    (a 'neutral' verdict is evidence that was weighed and found not to move
    anything — the case where the reasoning is the whole of the value). The
    RETURN stays the count of conviction updates, which is what the digest
    reports and what the KnowledgeCommit field is named for."""
    if not post_result.evaluations:
        return 0
    # An unknown strategy_id is DROPPED, not written. It was harmless while this
    # only ran an UPDATE (zero rows matched, silently); `evaluation.strategy_id`
    # is a REFERENCES with foreign_keys=ON, so one hallucinated id would now
    # abort the whole knowledge commit — the confrontations, the scenarios and
    # the innovations along with it.
    known = {str(r["id"]) for r in await db.query("SELECT id FROM strategy")}
    evaluations = []
    for ev in post_result.evaluations:
        if ev.strategy_id in known:
            evaluations.append(ev)
        else:
            logger.warning("evaluation names unknown strategy '%s', dropped", ev.strategy_id)
    if not evaluations:
        return 0
    now = datetime.now(UTC).isoformat()
    committed = 0
    async with db.transaction() as tx:
        await tx.append_event(
            type=EVALUATION_EVENT,
            source_uc=SOURCE_UC,
            source_id=None,
            payload={
                "evaluations": [
                    {"strategy_id": e.strategy_id, "verdict": e.verdict} for e in evaluations
                ]
            },
            event_date=today,
        )
        for ev in evaluations:
            # UPDATES is the FK on the child (docs/DATA_MODELS.md "Evaluation
            # -[UPDATES]-> Strategy -> evaluation.strategy_id"), so the vertex
            # write IS the edge — there is no separate create_edge call.
            await tx.create_vertex(
                "evaluation",
                {
                    "strategy_id": ev.strategy_id,
                    "date": today.isoformat(),
                    "verdict": ev.verdict,
                    "conviction_delta": ev.conviction_delta,
                    "events": ev.events,
                    "reasoning": ev.reasoning,
                    "trace": f"UC8 Planner Post evaluation of {ev.strategy_id} on {today}",
                },
            )
            if ev.conviction_delta == 0.0:
                continue
            await tx.command(
                "UPDATE strategy SET conviction = MAX(0, MIN(100, conviction + :d)), "
                "updated_at = :now WHERE id = :id",
                d=ev.conviction_delta,
                now=now,
                id=ev.strategy_id,
            )
            committed += 1
    return committed


async def _commit_scenario_updates(
    db: InvestmentDB, post_result: PostPlannerResult, today: date
) -> int:
    """New scenario probabilities (docs/ARCHITECTURE.md scenario updates).
    Call 2 names updates as (strategy, bull|base|bear); the stored row is keyed
    by the scenario's ID, so this resolves name -> id via the `scenario` table.
    COHERENCE GATE: a strategy's updates commit only if all THREE scenarios are
    present AND sum to 100 (the three-probabilities-sum-to-100 invariant) — a
    partial or incoherent update is skipped, not half-written."""
    if not post_result.scenario_updates:
        return 0
    by_strategy: dict[str, dict[str, float]] = {}
    for sc in post_result.scenario_updates:
        by_strategy.setdefault(sc.strategy_id, {})[sc.scenario] = sc.probability

    eligible: dict[str, dict[str, float]] = {}  # strategy -> {scenario_id: probability}
    for sid, by_name in by_strategy.items():
        if set(by_name) != _SCENARIO_KINDS:
            continue
        if abs(sum(by_name.values()) - 100.0) > _SCENARIO_SUM_TOLERANCE:
            continue
        rows = await db.query("SELECT id, name FROM scenario WHERE strategy_id = :s", s=sid)
        name_to_id = {str(r["name"]): str(r["id"]) for r in rows}
        if not set(name_to_id) >= _SCENARIO_KINDS:
            continue
        eligible[sid] = {name_to_id[name]: prob for name, prob in by_name.items()}

    if not eligible:
        return 0
    committed = 0
    async with db.transaction():
        await db.append_event(
            type=SCENARIO_EVENT,
            source_uc=SOURCE_UC,
            source_id=None,
            payload={"strategies": sorted(eligible)},
            event_date=today,
        )
        for scenario_probs in eligible.values():
            for scenario_id, probability in scenario_probs.items():
                await db.command(
                    "INSERT OR REPLACE INTO scenario_probability "
                    "(strategy_id, scenario, ts, probability) VALUES "
                    "((SELECT strategy_id FROM scenario WHERE id = :sc), :sc, :ts, :p)",
                    sc=scenario_id,
                    ts=today.isoformat(),
                    p=probability,
                )
                committed += 1
    return committed


def _safe_float(value: Any, default: float) -> float:
    """A spec number as a float, `default` when the model put prose in the field.
    Every `spec` value is LLM-authored, so a bare `float()` here would raise and
    abort the whole Monday chain over one malformed innovation."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _resolve_fk(db: InvestmentDB, table: str, value: Any, default: str | None) -> str | None:
    """A spec-supplied FK id, but only if the row EXISTS — else `default`.
    `INSERT OR IGNORE` does NOT absorb foreign-key violations in SQLite (ON
    CONFLICT covers UNIQUE/NOT NULL/CHECK/PK only), so an id the model invented
    would raise IntegrityError and abort the chain. Validating beats catching:
    the innovation still lands, minus the unresolvable reference."""
    if value is None:
        return default
    rows = await db.query(f"SELECT 1 FROM {table} WHERE id = :id", id=str(value))
    if rows:
        return str(value)
    logger.warning("innovation spec named unknown %s '%s' — falling back", table, value)
    return default


async def _resolve_framework(db: InvestmentDB, spec_value: Any) -> str | None:
    """The framework a new strategy belongs to. `framework_id` is NOT NULL, so
    unlike `regime_type_id` it cannot degrade to NULL: the spec's id if it exists,
    else the primary framework, else any framework at all. `None` only on a DB
    with no frameworks (unseeded) — the caller then skips the create instead of
    raising an FK error mid-chain."""
    resolved = await _resolve_fk(db, "framework", spec_value, None)
    if resolved is None:
        resolved = await _resolve_fk(db, "framework", DEFAULT_FRAMEWORK, None)
    if resolved is None:
        rows = await db.query("SELECT id FROM framework ORDER BY id LIMIT 1")
        resolved = str(rows[0]["id"]) if rows else None
    return resolved


async def _commit_strategy_innovation(
    db: InvestmentDB, proposal: ImprovementProposal, today: date
) -> str | None:
    """Create a proposed Strategy vertex from a new_strategy / strategy_revision
    innovation (docs/ARCHITECTURE.md "System Evolution"; docs/TASKS.md Phase 6).
    Born `status='proposed'`, `enabled=false` — it enters mechanical probation
    (outcomes.strategy_probation_check) and is activated there on PASS; nothing
    is enabled by the mere proposal (ADR-006). A revision records its lineage in
    `trace`; the superseded vertex is closed only on probation PASS, not here.
    Returns the new strategy id, or None when no framework can be resolved (see
    `_resolve_framework`).

    The InnovationEvent payload carries the FULL spec, because activation at
    +probation needs it: ARCHITECTURE's activation transaction creates the 3
    Scenario vertices and the BACKED_BY edges from this spec, and the strategy row
    itself has nowhere to keep them while the vertex is still proposed.

    A CANDIDATE PORTFOLIO is created with it (`_commit_candidate_portfolio`),
    because otherwise probation can never reach a verdict: `strategy_probation_check`
    judges the strategy on its FAVORS standing, FAVORS come from Backtests,
    Backtests need a NAV, and a NAV needs a Portfolio. Born with none, the
    strategy waited for evidence that could not exist — proposed forever, which
    ADR-006 forbids."""
    spec = proposal.spec or {}
    strategy_id = str(spec.get("id") or f"strat-{ULID()}")
    now = datetime.now(UTC).isoformat()
    supersedes = spec.get("supersedes")
    trace = proposal.trace + (f" [supersedes {supersedes}]" if supersedes else "")
    regime_type_id = await _resolve_fk(db, "regime_type", spec.get("regime_type_id"), None)
    framework_id = await _resolve_framework(db, spec.get("framework_id"))
    if framework_id is None:
        logger.warning("strategy innovation '%s' skipped: no framework exists", proposal.title)
        return None
    async with db.transaction():
        await db.append_event(
            type=INNOVATION_EVENT,
            source_uc=SOURCE_UC,
            source_id=strategy_id,
            payload={
                "type": proposal.type,
                "title": proposal.title,
                "supersedes": supersedes,
                "spec": spec,
            },
            event_date=today,
        )
        await db.command(
            "INSERT OR IGNORE INTO strategy (id, title, description, regime_type_id, framework_id, "
            "conviction, enabled, conditions, source, status, date_opened, trace, created_at, "
            "updated_at) VALUES (:id, :title, :desc, :rt, :fw, :conv, 0, :cond, 'agent-discovery', "
            "'proposed', :today, :trace, :now, :now)",
            id=strategy_id,
            title=proposal.title,
            desc=proposal.rationale,
            rt=regime_type_id,
            fw=framework_id,
            conv=_safe_float(spec.get("conviction"), DEFAULT_CONVICTION),
            cond=str(spec.get("conditions", "")),
            today=today.isoformat(),
            trace=trace,
            now=now,
        )
        await _commit_candidate_portfolio(db, strategy_id, spec, today, now)
    # The 35y NAV backfill runs AFTER the transaction: it is derived data
    # (`append_ts_batch` is INSERT OR REPLACE, so a re-run rebuilds it), and
    # holding a write transaction open across a full-history synthesis would
    # block the single writer for the whole cycle. Same split as the seed's.
    await _backfill_candidate_nav(db, strategy_id)
    return strategy_id


def candidate_portfolio_id(strategy_id: str) -> str:
    """The candidate Portfolio's id. Derived from the strategy id rather than
    stored, so probation, the backtests and the tests all name the same row
    without a lookup."""
    return f"{strategy_id}-candidate"


def _base_allocation(spec: dict[str, Any]) -> dict[str, float] | None:
    """The candidate book: the BASE scenario's `target_allocation`.

    Base, not a probability-weighted blend of the three — owner decision
    2026-08-02. It is the one allocation the strategy asserts as its central
    case; a blend would measure a portfolio the strategy never claims to hold,
    and would move as the probabilities are re-estimated, so the same strategy
    would be judged on a different book each week.

    `None` when the spec carries no usable base allocation. That is a MALFORMED
    spec, not an error to raise: the innovation is still recorded, the strategy
    is still born, and probation's unmeasurable backstop closes it at the window
    rather than the system refusing a proposal it was told to measure (ADR-006).

    "Usable" is the SHAPE checks a reallocation's `proposed_allocation` gets
    before it may be persisted, and for the identical reason — this is
    LLM-authored numbers on their way into a Portfolio row. Casting to float and
    stopping there let through everything `allocation_well_formed` was written
    to catch (gates.py: NaN slips past every later comparison, a negative leg is
    a short V1 cannot hold) plus a book whose weights sum to 7 or to 4000, which
    prices a candidate NAV on a leverage the strategy never claimed and feeds
    the resulting Sortino to FAVORS.

    Refused here rather than corrected: a reallocation gets ONE mechanical
    correction path (the 2.5-point blend re-normalization) and that operates on
    a delta the gates already accepted. Silently re-scaling a book that sums to
    7 would invent the strategy's central case rather than measure it, and
    probation is entitled to say "this spec carried no allocation" instead."""
    scenarios = spec.get("scenarios")
    if not isinstance(scenarios, list):
        return None
    for scenario in scenarios:
        if not isinstance(scenario, dict) or scenario.get("name") != "base":
            continue
        raw = scenario.get("target_allocation")
        if not isinstance(raw, dict) or not raw:
            return None
        try:
            allocation = {str(k): float(v) for k, v in raw.items()}
        except (TypeError, ValueError):
            return None
        if not allocation_well_formed(allocation):
            return None
        if abs(sum(allocation.values()) - 100.0) > ALLOCATION_SUM_TOLERANCE:
            return None
        return allocation
    return None


async def _commit_candidate_portfolio(
    db: InvestmentDB, strategy_id: str, spec: dict[str, Any], today: date, now: str
) -> None:
    """The candidate Portfolio a proposed strategy is measured through, created
    inside the birth transaction with its primary HOLDS edge.

    `enabled=0, defender=0` is what keeps ARCHITECTURE's rule intact — "a new
    Strategy affects the ranking only when a Portfolio HOLDS it; creating or
    modifying Portfolios remains a user preference (UC9)". The ranking, UC6
    valuation and UC7 all read `enabled = 1`, so this row is invisible to them.
    It exists for one purpose: to carry a NAV series so the strategy can be
    BACKTESTED before it is activated, which is the strategy analogue of what
    `mature_seed_invariants` does for an invariant born the same week. The 3
    disabled `ms-*-book` rows are the existing precedent for a Portfolio that is
    a measurement object rather than a holding.

    Caps are the user's, not looser (CLAUDE.md "Binding caps": per-portfolio
    rules may only be STRICTER). Currency, benchmark and phase come from the
    user profile rather than being invented here. Skipped when the spec's base
    allocation is unusable — the SHAPE checks in `_base_allocation`, and the two
    below that need the DB.

    The candidate is not a held book, so it is tempting to skip its caps. It is
    not held, but it IS measured, and everything downstream of the measurement
    binds: its NAV feeds `backtests` and from there FAVORS, and FAVORS is what
    the reallocation blend leans on. A candidate that is 100% one ticker
    produces the concentrated Sortino the user's cap exists to keep out of the
    system, then lends it to a proposal that never holds that book itself. Same
    for an unknown ticker: it has no price series, so the NAV is empty and the
    strategy is unmeasurable — better named at birth than diagnosed 24 weeks
    later by probation's backstop."""
    allocation = _base_allocation(spec)
    if allocation is None:
        logger.warning("strategy '%s': no base allocation in spec, no candidate NAV", strategy_id)
        return
    profile = await db.query(
        "SELECT currency, benchmark, phase, max_drawdown_pct, max_single_asset_pct "
        "FROM user_profile LIMIT 1"
    )
    if not profile:
        logger.warning("strategy '%s': no user_profile, no candidate portfolio", strategy_id)
        return
    user = profile[0]
    allowed = await _allowed_reallocation_tickers(db)
    unknown = sorted(set(allocation) - allowed)
    if unknown:
        logger.warning(
            "strategy '%s': base allocation holds untradable %s, no candidate portfolio",
            strategy_id,
            unknown,
        )
        return
    # Through `effective_caps`, not a hand-built `Caps`: it is this codebase's
    # single owner of the cap algebra, so if a candidate book ever grows its own
    # rule (the very columns the INSERT below writes) the stricter-of applies
    # here without anyone remembering to come back. `None` today — the row does
    # not exist yet at this point.
    caps = effective_caps(user, None)
    if not concentration_ok(allocation, caps):
        logger.warning(
            "strategy '%s': base allocation breaches the %.0f%% concentration cap, "
            "no candidate portfolio",
            strategy_id,
            caps.max_single_asset_pct,
        )
        return
    framework = await db.query("SELECT framework_id FROM strategy WHERE id = :id", id=strategy_id)
    await db.command(
        "INSERT OR IGNORE INTO portfolio (id, name, framework_id, defender, enabled, currency, "
        "benchmark, allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, "
        "updated_at) VALUES (:id, :name, :fw, 0, 0, :cur, :bench, :alloc, :dd, "
        ":cap, :phase, :trace, :now)",
        id=candidate_portfolio_id(strategy_id),
        name=f"Candidate book — {strategy_id}",
        fw=str(framework[0]["framework_id"]),
        cur=str(user["currency"]),
        bench=str(user["benchmark"]),
        alloc=json.dumps(allocation),
        dd=float(user["max_drawdown_pct"]),
        cap=float(user["max_single_asset_pct"]),
        phase=str(user["phase"]),
        trace=(
            f"Candidate book of the proposed strategy {strategy_id}, from its BASE scenario's "
            "target_allocation. Disabled: it exists so the strategy can be backtested during "
            "probation, not to be held (ADR-006 — nothing stays proposed forever)."
        ),
        now=now,
    )
    await db.create_edge(
        "holds",
        candidate_portfolio_id(strategy_id),
        strategy_id,
        {"is_primary": True, "since": today.isoformat()},
    )


async def _backfill_candidate_nav(db: InvestmentDB, strategy_id: str) -> None:
    """The candidate's 35y NAV, on the same engine and the same cost rate as
    every series it will be compared against (ADR-010). No rows when the
    portfolio was skipped or a sleeve has no prices — `backfill_nav` returns an
    empty result rather than raising, and probation's backstop is what resolves
    a strategy that stays unmeasurable.

    Built ONCE, at birth. That covers the common case — FAVORS aggregates
    Backtests over COMPLETED historical Regime instances, all of them before
    this date, so a candidate NAV that stops here still covers every window the
    probation verdict reads, and nothing values or ranks a disabled row.

    It does NOT cover the 12 weeks that follow: a regime can close inside the
    window, prices can arrive late, and the NAV never extends to meet either.
    `backtests` then accepts any slice with two observations and applies no
    minimum to the `overlap_pct` it records. Refreshing this before the weekly
    sweep, and gating on coverage, is I-51."""
    portfolio_id = candidate_portfolio_id(strategy_id)
    rows = await db.query("SELECT allocation FROM portfolio WHERE id = :id", id=portfolio_id)
    if not rows:
        return
    allocation = {str(k): float(v) for k, v in json.loads(str(rows[0]["allocation"])).items()}
    # The pinned window, read from `system_thresholds` rather than re-declared:
    # a candidate compared against its peers on a DIFFERENT lookback would be
    # judged on indicators that are not the ones it is judged against.
    window = await db.query("SELECT value FROM system_thresholds WHERE key = 'rolling_window_days'")
    result = await ratios.backfill_nav(
        db,
        portfolio_id,
        allocation,
        int(window[0]["value"]) if window else 756,
        ratios.TRADING_COST_BPS,
    )
    if result.rows_written == 0:
        logger.warning("strategy '%s': candidate NAV is empty (no prices?)", strategy_id)


async def _commit_invariant_innovation(
    db: InvestmentDB,
    proposal: ImprovementProposal,
    embedder: Embedder,
    corpus: InvariantCorpus,
    today: date,
) -> str | None:
    """Persist a new_invariant innovation through the SHARED dedup gate
    (writeback/knowledge.py `find_duplicate`) — the SAME gate the curator uses,
    so a Worker-proposed invariant and a curator-extracted one dedup against the
    corpus identically. On a duplicate the invariant is NOT re-created (an
    InnovationEvent records the merge target); otherwise it is born
    status='proposed' and matured over 35y by the caller. Returns the new id, or
    None when merged.

    `corpus` is GROWN in place with whatever this call created, because the gate
    can only refuse what it can see: one Worker call returns a LIST of
    innovations, and a corpus read once before the loop makes every member of
    that batch invisible to the next. Two paraphrases of the same claim in one
    `innovations_proposed` both matched nothing and both were created, which is
    precisely the duplicate this gate exists to stop. The curator grows its
    corpus inside its own batch loop for this reason (knowledge.py `persist`);
    this is the same move on the UC8 path."""
    spec = proposal.spec or {}
    condition = spec.get("condition", [])
    effect = spec.get("effect")
    title, description = proposal.title, proposal.rationale
    vector = embedder.encode([invariant_embedding_input(title, description)])[0]

    match = find_duplicate(
        vector, condition, effect, corpus, DEDUP_COSINE_THRESHOLD, label=title[:60]
    )
    if match is not None:
        async with db.transaction():
            await db.append_event(
                type=INNOVATION_EVENT,
                source_uc=SOURCE_UC,
                source_id=match,
                payload={"type": "new_invariant", "title": title, "merged_into": match},
                event_date=today,
            )
        return None

    # The tier's seeded band BINDS the weights, exactly as it does on the
    # curator path (`knowledge.author_band`) — the model proposes a number, the
    # band decides what it may be. Without this the Worker's own
    # `weight_initial`/`floor_weight` were written raw, and `ImprovementProposal`
    # defaults BOTH to 0.0 (worker/result.py) for the proposals that do not
    # bother to invent them: `weight_effective = max(0 x score x recency, 0)` is
    # zero forever, so the invariant was born already unable to influence
    # anything, and no amount of confirmation could lift it off the floor.
    band = await author_band(db, proposal.author)

    invariant_id = str(spec.get("id") or f"inv-{ULID()}")
    async with db.transaction() as tx:
        await tx.append_event(
            type=INNOVATION_EVENT,
            source_uc=SOURCE_UC,
            source_id=invariant_id,
            payload={"type": "new_invariant", "title": title},
            event_date=today,
        )
        await tx.create_vertex(
            "invariant",
            {
                "id": invariant_id,
                "title": title,
                "description": description,
                "source": "agent-discovery",
                "author": proposal.author,
                "status": "proposed",  # ADR-006: it earns its verdict from the 35y sweep
                "tags": spec.get("tags", []),
                "embedding": to_blob(vector),
                "condition": condition,
                "effect": effect,
                "weight_initial": band.bind(proposal.weight_initial),
                "floor_weight": band.floor,
                "trace": proposal.trace or "UC8 agent-discovery innovation",
            },
        )
    corpus.add(invariant_id, condition, effect, vector)
    return invariant_id


async def commit_innovations(
    db: InvestmentDB,
    post_result: PostPlannerResult,
    today: date,
    embedder: Embedder | None = None,
) -> int:
    """Commit the innovations the analysis proposed (docs/TASKS.md Phase 6,
    "Innovations"): new_strategy / strategy_revision -> a proposed, disabled
    Strategy vertex that enters probation; new_invariant -> the shared dedup
    gate then a proposed Invariant vertex, matured over 35y (needs the embedder;
    without it new_invariants are recorded as pending InnovationEvents).
    process / data -> InnovationEvent only (no V1 vertex type — I-27)."""
    committed = 0
    # Loaded lazily and ONCE, then grown in flight by each creation: `None` is
    # "not read yet", which an empty corpus (a legitimate state on a fresh DB)
    # could not express while this was a pair of variables.
    corpus: InvariantCorpus | None = None
    created_invariant = False

    for innovation in post_result.innovations:
        if innovation.type in _STRATEGY_INNOVATION_TYPES:
            if await _commit_strategy_innovation(db, innovation, today) is not None:
                committed += 1
        elif innovation.type == "new_invariant" and embedder is not None:
            if corpus is None:
                corpus = await load_invariant_corpus(db)
            new_id = await _commit_invariant_innovation(db, innovation, embedder, corpus, today)
            if new_id is not None:
                committed += 1
                created_invariant = True
        else:
            async with db.transaction():
                await db.append_event(
                    type=INNOVATION_EVENT,
                    source_uc=SOURCE_UC,
                    source_id=None,
                    payload={"type": innovation.type, "title": innovation.title, "pending": True},
                    event_date=today,
                )

    # Mature the new invariant(s) over 35y (fingerprint-guarded: only the fresh
    # ones sweep). Run once, after all creates, and only if any were created.
    if created_invariant:
        await mature_seed_invariants(db)
    return committed


async def commit_knowledge(
    db: InvestmentDB,
    post_result: PostPlannerResult,
    regime_type: str | None,
    thresholds: dict[str, float],
    today: date | None = None,
    embedder: Embedder | None = None,
) -> KnowledgeCommit:
    """Commit the guardrailed PostPlannerResult to the graph (docs/TASKS.md
    Phase 6). The guardrail already dropped every unknown id, every unevidenced
    verdict and every malformed/repeat confrontation, so this is pure mechanical
    persistence: source='evaluation' confrontations (weight-moving,
    condition-gated), the evaluation record + conviction nudges, the coherent
    scenario-probability updates (bull/base/bear -> scenario id), and the
    innovations (dedup gate + 35y maturation)."""
    today = today or date.today()
    active = await active_invariant_ids(
        db, [c.invariant_id for c in post_result.confrontations], regime_type
    )
    confrontations = await _commit_confrontations(
        db, post_result, regime_type, active, thresholds, today
    )
    conviction = await _commit_evaluations(db, post_result, today)
    scenarios = await _commit_scenario_updates(db, post_result, today)
    innovations = await commit_innovations(db, post_result, today, embedder=embedder)
    return KnowledgeCommit(
        confrontations=confrontations,
        conviction_updates=conviction,
        scenario_updates=scenarios,
        innovations=innovations,
    )
