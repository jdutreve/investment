"""The cognitive decision cycle (docs/USE_CASES.md UC8; docs/ARCHITECTURE.md
"09:00 UC8: Planner Pre → Worker → Planner Post → Writeback"). The first full
cognitive chain: it wires the three strictly-separated roles built in the
earlier M8 slices into one call.

Named for what it DOES, not for its spec coordinate — this file was `uc8.py`
until 2026-08-02. The UC number is a pointer into a document, and this project
has already superseded UC8's allocation role once (ADR-007); the reference
belongs in this docstring, where it cannot rot into a misleading filename. Its
sibling `market_signal_cycle.py` follows the same rule. Note that the EventLog
`source_uc` values stay 'UC8': those are committed DATA, append-only.

  PlannerPre.run        → PlannerContext (baseline + Call 1a margin + Call 1b)
  Worker                → WorkerResult (interprets, proposes)
  PlannerPost.run       → PostPlannerResult (extract + guardrail)
  Writeback.dispose     → Proposal, only if every gate passes

The Worker is handed the context as TEXT and stays unaware of the Planner,
Writeback and storage (docs/ARCHITECTURE.md WORKER) — `render_context_for_worker`
is that boundary. Writeback runs ONLY on what the Worker proposed: no
reallocation, no gate run, no vertex. The knowledge commit (confrontations,
conviction nudges, scenario probabilities, innovations) runs on every cycle,
proposal or not — a quiet week still learns. So does `journal_worker_reading`,
which records the Worker's prose (ADR-011) before the guardrail runs: without it
a cycle that proposes and confronts nothing leaves no trace at all.
"""

import dataclasses
import json
from datetime import date
from typing import Any

from pydantic_ai import Agent

from investment.db.sqlite import InvestmentDB
from investment.mechanical.market_signal import MA_WINDOW_DAYS, describe_rule
from investment.planner.context import PlannerContext
from investment.planner.post import PlannerPost, PostPlannerResult
from investment.planner.pre import PlannerPre
from investment.worker.agent import run_worker
from investment.worker.result import WorkerResult
from investment.writeback.writeback import SOURCE_UC, KnowledgeCommit, commit_knowledge

# The cycle's own journal entry (see `journal_worker_reading`). Declared here,
# beside the only thing that appends it, as `chain.ERROR_EVENT` and
# `outcomes.OUTCOME_EVENT` are — writeback.py owns the event names for what
# WRITEBACK persists, and this is not a disposition.
WORKER_READING_EVENT = "WorkerReadingEvent"

# The citation floor as the Worker is TOLD it (`_not_citable_because`). A copy
# of `system_thresholds.proposal_invariant_weight_min`, not a read of it: this
# is prompt text built from a context dict that carries no thresholds, while the
# gate itself reads the seeded value and stays the authority. If they ever
# disagree the gate wins and the Worker was merely misinformed — which is still
# strictly better than the previous state, where it was told nothing at all.
CITATION_WEIGHT_MIN = 0.10


@dataclasses.dataclass(frozen=True)
class UC8Result:
    """One decision cycle's full output — the context the Worker saw, its
    result, and the guardrailed knowledge it produced.

    NO DISPOSITION FIELDS since ADR-012: the Worker does not allocate, so a
    cycle has no gate outcome and mints no Proposal. What it leaves behind is
    the journalled reading and whatever knowledge survived the guardrail."""

    context: PlannerContext
    worker_result: WorkerResult
    post_result: PostPlannerResult
    knowledge: KnowledgeCommit  # what the guardrailed knowledge commit persisted


def _defender_row(ranking: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The defender among the ranked portfolios (docs/DATA_MODELS.md: exactly
    one defender). None if the snapshot carried none — then there is nothing to
    reallocate."""
    return next((row for row in ranking if row.get("defender")), None)


def _allocation(row: dict[str, Any]) -> dict[str, float]:
    alloc = row.get("allocation")
    if isinstance(alloc, str):
        alloc = json.loads(alloc)
    return {str(k): float(v) for k, v in (alloc or {}).items()}


def _market_signal_lines(state: dict[str, Any]) -> list[str]:
    """The live allocation state as the Worker reads it (ADR-007). Two things it
    must convey, and the phrasing carries both:

    1. this allocation is ALREADY DECIDED, mechanically, from a market-priced
       signal validated over 35 years. The Worker is told so plainly, because a
       model handed an allocation with no framing will treat it as a proposal to
       improve — and "improving" it is exactly the drift
       `mechanical/market_signal.py` exists to prevent;
    2. what the signal actually said, so the qualitative reading the Worker DOES
       own (docs/V1_STRATEGY.md Step 4: "the Worker nuances the monthly
       regime/book decision") has the numbers to work from.

    Empty before the first live decision — then the block is simply absent
    rather than asserting a state that does not exist yet.

    HELD AND TARGET ARE BOTH SHOWN, and separately. They are the same thing on
    a normal month, and they DIVERGE on exactly the months worth talking about:
    one where a gate refused the move (`gate` != passed, so the stack is frozen
    off its target) and the first one after such a refusal, which re-proposes
    it. Printing only the target under the label "effective allocation" told the
    Worker the stack held something it did not — the same error
    `outcomes._incumbent_allocation` exists to avoid on the scoring side."""
    if not state:
        return []
    signals = state.get("signals") or {}
    overlay = state.get("trend_overlay") or {}
    hysteresis = state.get("hysteresis") or {}
    held = state.get("held_allocation") or {}
    target = state.get("target_allocation") or {}
    gate = state.get("gate")
    lines = [
        "",
        "MARKET-SIGNAL ALLOCATION (already decided mechanically — do NOT re-pick "
        "the book; read it, and say where it looks wrong):",
        f"  book in force: {state.get('held_book', '?')} "
        f"(signal now: {state.get('signal_state', '?')}), "
        f"decided {state.get('decision_date', '?')}",
        f"  currently held: {held or '(nothing yet — this is the opening entry)'}",
    ]
    if gate and gate != "passed":
        lines.append(
            f"  target {target} was BLOCKED by a mechanical guard ({gate}); the stack is "
            "frozen off its target until the next monthly decision."
        )
    elif target != held:
        lines.append(f"  moving to: {target}")
    for ticker, read in signals.items():
        lines.append(
            f"  {ticker}: {read.get('value')} vs its 10y trailing median "
            f"{read.get('trailing_median')} (knowable {read.get('knowable_at')})"
        )
    below = overlay.get("below_trend") or []
    # The window comes off the DECISION's own payload (`trend_overlay.window_days`,
    # stamped by `market_signal_cycle.build_market_context`), never typed here:
    # this line said "200d" for a day after the window moved to 300, so the
    # Worker read one number in the state block and another in the generated rule
    # text below — the fourth stale-rule-text defect, this time contradicting
    # `describe_rule` inside a single prompt.
    window = overlay.get("window_days") or MA_WINDOW_DAYS
    lines.append(
        f"  {window}d trend overlay: {', '.join(below)} below trend, redirected to the haven"
        if below
        else f"  {window}d trend overlay: no sleeve below trend"
    )
    if hysteresis.get("pending_book"):
        lines.append(
            f"  pending switch to {hysteresis['pending_book']}: "
            f"{hysteresis.get('pending_count')}/{hysteresis.get('confirm_decisions')} confirmations"
        )
    # THE RULE, not just its output. The block above says what the instrument
    # DECIDED; this says how it works — and the Worker is asked to challenge the
    # rule, which it cannot do from memory. Measured across both M8b runs: it
    # twice described the overlay as covering "SPY only" while TREND_SLEEVES
    # includes GLD, on dates whose own logs printed below-trend=['SPY','GLD'].
    # Generated from the constants (`market_signal.describe_rule`), so it cannot
    # drift from the code the way a hand-copied description would.
    lines += ["", *describe_rule().splitlines()]
    return lines


async def _book_row(db: InvestmentDB, portfolio_id: str) -> dict[str, float] | None:
    """The book's CURRENT allocation, or None if the row is absent.

    Read from `portfolio` rather than from the weekly snapshot: the snapshot is
    a weekly run photograph, and this row moves the moment a reallocation passes
    (`writeback._commit_reallocation`). Comparing a new proposal against a stale
    incumbent would let the min-change and turnover gates judge against a book
    that is no longer held — the same error `outcomes._incumbent_allocation`
    exists to avoid on the scoring side.

    None when the row is missing, which is a fresh database rather than an
    error: the cycle is then knowledge-only, exactly as it was when no defender
    existed."""
    rows = await db.query("SELECT allocation FROM portfolio WHERE id = :id", id=portfolio_id)
    if not rows:
        return None
    parsed = rows[0]["allocation"]
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    return {str(k): float(v) for k, v in (parsed or {}).items()}


def _not_citable_because(inv: dict[str, Any]) -> str | None:
    """Why gate 6 would refuse this invariant as support, or None if it would
    not — the SAME four clauses as `gates.cited_invariant_eligible`, stated in
    the Worker's own words.

    Deliberately a second expression of one rule, and the duplication is the
    lesser evil: the gate is the authority and runs on the DB rows, while this
    reads the context dict the Worker is handed, which carries no confrontation
    counts. Keeping them in sync is a real cost; leaving the Worker to guess
    cost a cycle every time it cited a lighthouse that looked fine
    (docs/MILESTONES.md M8: "gate 6 may be near-unsatisfiable")."""
    if str(inv.get("status")) != "integrated":
        return f"status {inv.get('status')}, not integrated"
    if not inv.get("active"):
        return "dormant — its condition does not hold today"
    weight = inv.get("weight_effective")
    if weight is not None and float(weight) < CITATION_WEIGHT_MIN:
        return f"weight {float(weight):.2f} below the {CITATION_WEIGHT_MIN:.2f} floor"
    return None


def render_context_for_worker(context: PlannerContext) -> str:
    """The PlannerContext as the text the Worker reads (docs/ARCHITECTURE.md
    WORKER: "the data in your context"). Deliberately unaware of provenance —
    no Planner, no pool, no storage — just the market picture, the ranking, the
    lighthouses (with which are lit NOW), and the framing.

    NO BOOK IS NAMED HERE, and briefly one was. On 2026-08-09 this renderer
    grew a `YOUR BOOK` block, because the Worker had never been told which
    portfolio its reallocations moved; ADR-012 removed the reallocation itself
    hours later, and the block with it. What the Worker reads is the world and
    the mechanical decision — it holds nothing."""
    regime = context.regime
    lines = [
        f"REGIME: {regime.get('regime_name', '?')} ({regime.get('regime_type_id', '?')}), "
        f"confidence {regime.get('confidence', '?')}",
        f"GLOBAL LIQUIDITY: {context.global_liquidity}",
    ]
    lines.extend(_market_signal_lines(context.market_signal))
    # WHAT HAS ALREADY BEEN TRIED, and this is the fifth time this week that a
    # fact the system held was not reaching the only thing that could use it.
    # "Add VCIT to the trend overlay" arrived three times across independent
    # dates, each one after a measured rejection nothing could show it; the
    # Worker was not being stubborn, it was being kept ignorant.
    if context.measured_revisions:
        lines += ["", "ALREADY MEASURED over the history (do not re-propose these settings):"]
        for row in context.measured_revisions:
            deltas = ", ".join(
                f"{name} {row[key]:+.3f}"
                for name, key in (("sortino", "sortino_delta"), ("cagr", "cagr_delta"))
                if row.get(key) is not None
            )
            window = f"{str(row.get('window_start'))[:4]}-{str(row.get('window_end'))[:4]}"
            n = row.get("windows", 1)
            # MIXED is the loudest of the three: it means the change adopts on
            # one window and fails another, which is a fitted result and not a
            # finding.
            scope = f"{n} windows" if n > 1 else f"{window} only"
            lines.append(f"  {row['verdict'].upper():7} {row['overrides']}  [{scope}: {deltas}]")

    lines += [
        "",
        "RANKED PORTFOLIOS (defender marked *):",
    ]
    for row in context.ranking:
        star = " *" if row.get("defender") else ""
        alloc = _allocation(row) if row.get("allocation") else {}
        lines.append(
            f"  {row.get('rank')}. {row.get('portfolio_id')}{star} "
            f"sortino={row.get('sortino_rolling')} calmar={row.get('calmar_rolling')} "
            f"maxDD={row.get('max_drawdown')} alloc={alloc}"
        )
    lines.append("")
    lines.append("SCENARIOS (probability, week-over-week shift):")
    for sc in context.scenarios:
        # The NAME (bull/base/bear), not the Scenario id: it is what the Worker
        # and Call 2 must give back for a scenario update to resolve
        # (baseline._scenarios docstring).
        lines.append(
            f"  {sc.get('strategy_id')}/{sc.get('name') or sc.get('scenario')}: "
            f"{sc.get('probability')} ({sc.get('shift', 0.0):+})"
        )
    # THE MACRO TAPE AND THE REGIME STANDINGS, handed over rather than sold.
    # Measured across the two M8b runs: the Worker spent 37 tool calls
    # re-querying FAVORS and 15 fetching MACRO series, out of a budget meant for
    # checking the books and tickers it is about to name. Its context carried
    # exactly two macro readings — the regime label and global liquidity — while
    # the persona asks it to read the WEATHER.
    #
    # FAVORS is scoped to the CURRENT regime type (baseline `_favors`): the table
    # is per-regime precisely so a Sortino earned in stagflation is not compared
    # against a disinflation peer group.
    if context.macro:
        lines.append("")
        lines.append("MACRO TAPE (level / speed / acceleration, latest knowable):")
        for row in context.macro:
            lines.append(
                f"  {row.get('ticker')}: {row.get('level')} "
                f"(speed {row.get('speed')}, accel {row.get('acceleration')}) "
                f"as of {row.get('ts')}"
            )
    if context.favors:
        lines.append("")
        lines.append(
            f"STRATEGY STANDINGS in {context.regime.get('regime_type_id', 'this regime')} "
            "(FAVORS, sortino DESC):"
        )
        for row in context.favors:
            lines.append(
                f"  {row.get('strategy_id')}: sortino {row.get('sortino_rolling')}, "
                f"calmar {row.get('calmar_rolling')}, maxDD {row.get('max_drawdown')} "
                f"over n={row.get('n_periods')}"
            )
    lines.append("")
    lines.append("INVARIANTS (lighthouses — [CITABLE] may support a reallocation):")
    citable = 0
    for inv in context.top_invariants:
        reason = _not_citable_because(inv)
        citable += reason is None
        flag = "[CITABLE]" if reason is None else f"[not citable: {reason}]"
        lines.append(
            f"  {flag} {inv.get('id')} — {inv.get('title', '')} "
            f"(weight {inv.get('weight_effective', '?')}, {inv.get('author', 'null')}, "
            f"{inv.get('status', '?')})"
        )
    # THE REASON, not just the verdict. The Worker used to be told "cite only
    # ACTIVE and integrated" and left to infer the rest — it was never told the
    # weight floor or the refuted test, so an [ACTIVE] integrated lighthouse
    # could still fail gate 6 for a reason invisible to it.
    #
    # AND IT IS TOLD WHEN THE SET IS EMPTY. Measured on the M8b covid episode:
    # it proposed three times and was refused twice on gate 6, with nothing
    # citable available whatever it chose. Proposing into a vacuum is not a
    # reasoning error, and the cycle it costs is a cycle nobody could have
    # spent well.
    if not context.top_invariants:
        pass
    elif citable:
        lines.append(
            f"  → {citable} citable. A reallocation MUST cite at least one of them; "
            "the others are shown for reasoning, not for support."
        )
    else:
        lines.append(
            "  → NONE is citable this cycle. A reallocation cannot be supported and "
            "will be refused whatever it cites — this is a fact about the corpus, "
            "not about your reading. Your contribution this month is the assessment "
            "and any innovation you file."
        )
    if context.passages:
        lines.append("")
        lines.append("RELEVANT PASSAGES:")
        for p in context.passages:
            lines.append(f"  {str(p.get('excerpt', ''))[:200]}")
    if context.notes:
        lines.append("")
        lines.append(f"COACH NOTES: {context.notes}")
    return "\n".join(lines)


def _market_context(context: PlannerContext) -> dict[str, Any]:
    """The compact market snapshot stamped on the Proposal (docs/DATA_MODELS.md
    Proposal.market_context)."""
    return {
        "regime": context.regime.get("regime_type_id"),
        "regime_name": context.regime.get("regime_name"),
        "confidence": context.regime.get("confidence"),
        "global_liquidity": context.global_liquidity,
    }


async def journal_worker_reading(
    db: InvestmentDB,
    worker_result: WorkerResult,
    context: PlannerContext,
    *,
    trigger: str,
    run_id: str | None,
    today: date | None = None,
) -> None:
    """Journal the Worker's PROSE — the only trace a cycle that proposes nothing
    would otherwise leave.

    ADR-011 says the Worker's qualitative reading of the mechanical decision is
    "journalled and rendered"; this is the journalling half. It covers the whole
    prose surface rather than that one field, because the hole was never specific
    to it: `regime_assessment` and `ranking_commentary` had NO reader anywhere
    outside the Planner-Post prompt they are serialised into, and UC8 appended no
    cycle-level event at all — so a week that confronted nothing and proposed
    nothing vanished completely, indistinguishable from a week that never ran.

    Appended on EVERY cycle, before the knowledge commit, in its own transaction.
    It records what the Worker SAID, which is a fact regardless of what the
    guardrail later kept: a reading the Planner Post dropped is exactly the one
    an audit wants to find.

    `market_signal_decision_date` is the anchor of the decision the Worker was
    actually shown, so the reading can be joined back to the month it judges —
    without it, a critique read six months later cannot be told from a stale one.
    None when the context carried no market-signal decision yet."""
    market_signal = context.market_signal or {}
    async with db.transaction():
        await db.append_event(
            type=WORKER_READING_EVENT,
            source_uc=SOURCE_UC,
            source_id=run_id,
            payload={
                "trigger": trigger,
                "run_id": run_id,
                "market_signal_decision_date": market_signal.get("decision_date"),
                "market_signal_assessment": worker_result.market_signal_assessment,
                "regime_assessment": worker_result.regime_assessment,
                "ranking_commentary": worker_result.ranking_commentary,
                "reasoning": worker_result.reasoning,
            },
            event_date=today,
        )


async def run_decision_cycle(
    db: InvestmentDB,
    planner_pre: PlannerPre,
    worker_agent: Agent[None, WorkerResult],
    planner_post: PlannerPost,
    *,
    trigger: str,
    user_profile: dict[str, Any],
    thresholds: dict[str, float],
    today: date | None = None,
    run_id: str | None = None,
    context: PlannerContext | None = None,
) -> UC8Result:
    """Run one UC8 cycle end to end. Writeback only runs if the Worker proposed
    a reallocation AND a defender exists to reallocate; otherwise the cycle is
    knowledge-only (gate_outcome / proposal_id stay None). Returns everything
    the digest renders.

    `run_id` is the scheduled run's id (CLAUDE.md "Dev standards": one per
    scheduled run) and is stamped on the journalled reading. Optional because
    nothing assembles the weekly chain yet (M9) — an ad-hoc UC9 re-run has no
    run id to give.

    `context` SKIPS the Planner's pre-phase when the caller already holds one.
    For a RETRY, not for a shortcut: the pre-phase is two LLM calls over a
    snapshot that has not changed between attempts, so redoing it after a Worker
    failure re-buys an identical answer — and spends it out of the same bounded
    budget the first attempt just proved too short (agentic_replay
    `_cycle_with_retry`). The parameter exists so the harness can re-run only
    what failed WITHOUT reimplementing the cycle, which Task 9.4 forbids.
    Default `None` keeps the live weekly chain exactly as it was.

    NOTHING IS DISPOSED HERE since ADR-012. The cycle runs the three roles,
    journals the reading and commits the guardrailed knowledge; there is no
    reallocation to gate, so no Proposal is minted and no book moves."""
    context = context or await planner_pre.run(trigger)
    worker_result = await run_worker(worker_agent, render_context_for_worker(context))
    await journal_worker_reading(
        db, worker_result, context, trigger=trigger, run_id=run_id, today=today
    )
    post_result = await planner_post.run(worker_result, context)

    regime_type = context.regime.get("regime_type_id")
    knowledge = await commit_knowledge(
        db, post_result, regime_type, thresholds, today=today, embedder=planner_pre.embedder
    )

    return UC8Result(context, worker_result, post_result, knowledge)
