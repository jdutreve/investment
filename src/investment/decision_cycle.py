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
  Worker                → WorkerResult (interprets; proposes KNOWLEDGE)
  PlannerPost.run       → PostPlannerResult (extract + guardrail)
  commit_knowledge      → confrontations, conviction, scenarios, innovations

NO DISPOSITION AND NO GATE, since ADR-012 (the fourth line above read
"Writeback.dispose → Proposal, only if every gate passes" until 2026-08-14).
The Worker does not allocate, so this chain mints no Proposal: the month's one
Proposal comes from `market_signal_cycle`, mechanically, at 08:55 — this cycle
READS it and comments on it.

The Worker is handed the context as TEXT and stays unaware of the Planner,
Writeback and storage (docs/ARCHITECTURE.md WORKER) — `render_context_for_worker`
is that boundary. The knowledge commit (confrontations, conviction nudges,
scenario probabilities, innovations) runs on every cycle — a quiet week still
learns. So does `journal_worker_reading`, which records the Worker's prose
(ADR-011) before the guardrail runs: without it a cycle that confronts nothing
leaves no trace at all.
"""

import dataclasses
import json
from datetime import date
from typing import Any

from pydantic_ai import Agent

from investment.db.sqlite import InvestmentDB
from investment.mechanical.market_signal import MA_WINDOWS, describe_rule
from investment.planner.context import PlannerContext
from investment.planner.post import PlannerPost, PostPlannerResult
from investment.planner.pre import PlannerPre
from investment.worker.agent import run_worker
from investment.worker.result import WorkerResult
from investment.worker.tools import round_for_model
from investment.writeback.writeback import SOURCE_UC, KnowledgeCommit, commit_knowledge

# The cycle's own journal entry (see `journal_worker_reading`). Declared here,
# beside the only thing that appends it, as `chain.ERROR_EVENT` and
# `outcomes.OUTCOME_EVENT` are — writeback.py owns the event names for what
# WRITEBACK persists, and this is not a disposition.
WORKER_READING_EVENT = "WorkerReadingEvent"

# THE CYCLE FINISHED — appended last, after `commit_knowledge` returned. It
# exists because the weekly resume guard (`weekly.weekly_cycle_already_ran`)
# needs to know whether UC8 COMPLETED, and the only marker available was the
# reading above, which is journalled BEFORE Planner Post and Writeback have run.
# A cycle whose Worker answered and whose knowledge commit then failed left that
# reading behind, so the resume after the crash read "already done" and skipped
# the half that had not happened — the guard against a duplicate deliberation
# had become a guard against ever finishing one.
#
# Two events rather than a moved one: the reading is an AUDIT of what the Worker
# said and has to survive a later failure (it is what the next attempt's context
# and any post-mortem read), while this is a TRANSACTION MARKER. Making one row
# mean both is what caused the bug.
CYCLE_COMPLETED_EVENT = "CognitiveCycleCompletedEvent"


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


def _allocation(row: dict[str, Any]) -> dict[str, float]:
    alloc = row.get("allocation")
    if isinstance(alloc, str):
        alloc = json.loads(alloc)
    return {str(k): float(v) for k, v in (alloc or {}).items()}


def _market_signal_lines(state: dict[str, Any], macro: list[dict[str, Any]]) -> list[str]:
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
    `outcomes._incumbent_allocation` exists to avoid on the scoring side.

    EACH SIGNAL IS PRINTED ON BOTH CLOCKS, and that is what `macro` is for.
    The decision is MONTHLY (ADR-007): its inputs are frozen at the anchor
    date, and they are the only numbers that may justify the book in force. The
    macro tape is TODAY, up to a month newer, and it is the best available
    proxy for what the NEXT monthly decision will see. Both were already in the
    prompt and both were already dated — in two separate blocks, which was not
    enough. Measured 2026-08-23: the Worker wrote "0.45 vs a 0.41 median is 4bp
    of headroom, and T10Y2Y's own speed (+0.14) is the only thing keeping this
    book from flipping flat", taking the LEVEL off the decision (2026-08-01)
    and the SPEED off the live tape (2026-08-22). Those two numbers never
    coexisted: live the pair was 0.50/+0.14, ~9bp of headroom and steepening.
    It reported a boundary state the tape had already resolved, from two
    correct numbers on two different clocks. Pairing them on ONE line is what
    makes the drift impossible to miss and "what will the next decision see" an
    answerable question."""
    if not state:
        return []
    latest = {str(row.get("ticker")): row for row in macro}
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
        "  TWO CLOCKS BELOW, keep them apart. 'DECIDED ON' is what the monthly "
        "decision actually saw and the ONLY figure that explains the book in "
        "force. 'now' is today's tape — it decided nothing, and it is your best "
        "proxy for what the NEXT monthly decision will see. Never mix a level "
        "from one with a speed from the other.",
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
            f"  {ticker}: DECIDED ON {round_for_model(read.get('value'))} vs its 10y "
            f"trailing median {round_for_model(read.get('trailing_median'))} "
            f"(knowable {read.get('knowable_at')})"
        )
        now = latest.get(ticker)
        if now is None:
            continue
        # The DRIFT stated, not left to be subtracted. The median moves in basis
        # points over a month (10y trailing), so comparing today's level against
        # the DECISION's median is the right read for "where would this land
        # now", and saying it in words is what stops the two clocks being mixed.
        median, level = read.get("trailing_median"), now.get("level")
        drift = ""
        if isinstance(median, int | float) and isinstance(level, int | float):
            side = "above" if level > median else "below"
            drift = f", {abs(level - median):.2f} {side} that median"
        lines.append(
            f"    now {round_for_model(level)} (speed {round_for_model(now.get('speed'))}, "
            f"accel {round_for_model(now.get('acceleration'))}) as of {now.get('ts')}{drift}"
        )
    below = overlay.get("below_trend") or []
    # The window comes off the DECISION's own payload (`trend_overlay.window_days`,
    # stamped by `market_signal_cycle.build_market_context`), never typed here:
    # this line said "200d" for a day after the window moved to 300, so the
    # Worker read one number in the state block and another in the generated rule
    # text below — the fourth stale-rule-text defect, this time contradicting
    # `describe_rule` inside a single prompt.
    # BOTH SHAPES, because both are in the log. `window_days` was an int on
    # every row until 2026-08-14; the graduated overlay writes `windows_days` as
    # a list instead of changing that field's type under committed history. A
    # row carries one or the other, and an old row must still render.
    windows = overlay.get("windows_days")
    window = "/".join(str(w) for w in windows) if windows else overlay.get("window_days")
    if not window:
        window = "/".join(str(w) for w in MA_WINDOWS)
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
    lines.extend(_market_signal_lines(context.market_signal, context.macro))
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
        lines.append(
            "MACRO TAPE — TODAY'S CLOCK (level / speed / acceleration, latest knowable). "
            "None of this decided the book in force; it is the proxy for what the NEXT "
            "monthly decision will see, and it is where your forward reading belongs. "
            "Each line carries its own date — a monthly series is normally weeks old, "
            "so check the date before calling a number current:"
        )
        for row in context.macro:
            lines.append(
                f"  {row.get('ticker')}: {round_for_model(row.get('level'))} "
                f"(speed {round_for_model(row.get('speed'))}, "
                f"accel {round_for_model(row.get('acceleration'))}) "
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
    # THE CORPUS TO REASON WITH, no longer a set of citable supports. Until
    # ADR-012 each line carried a [CITABLE] / [not citable: reason] flag and a
    # closing sentence — "a reallocation MUST cite at least one of them" — that
    # taught gate 6 to a Worker which could then satisfy it. The Worker no longer
    # allocates, `WorkerResult` has no field to carry a citation, and nothing
    # writes `proposal_cites` on the live path, so the whole apparatus was
    # instructing the model in a move it cannot make. DORMANT is kept, because it
    # is a fact about the market rather than about a gate: a lighthouse whose
    # condition does not hold today describes a world that is not present, which
    # is exactly what a reader needs to weigh it.
    lines.append("INVARIANTS (lighthouses — the corpus you reason with):")
    for inv in context.top_invariants:
        state = f"{inv.get('status', '?')}" + ("" if inv.get("active") else ", dormant")
        lines.append(
            f"  {inv.get('id')} — {inv.get('title', '')} "
            f"(weight {inv.get('weight_effective', '?')}, {inv.get('author', 'null')}, "
            f"{state})"
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
    thresholds: dict[str, float],
    today: date | None = None,
    run_id: str | None = None,
    context: PlannerContext | None = None,
) -> UC8Result:
    """Run one UC8 cycle end to end: the three roles, the journalled reading and
    the guardrailed knowledge commit. Returns everything the digest renders.

    The cycle is KNOWLEDGE-ONLY since ADR-012 (`gate_outcome` / `proposal_id`
    stay None on every path — see the paragraph below). `thresholds` therefore
    reaches `commit_knowledge` rather than a gate, and `user_profile` is gone
    from this signature: the binding caps had exactly one consumer here, the
    reallocation disposition, and a parameter every caller still assembles for
    a function that ignores it is a promise the code stopped keeping.

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

    # LAST, and only on the success of everything above — this is what the
    # weekly resume reads to decide the cycle need not run again
    # (`CYCLE_COMPLETED_EVENT`). Anything that raised before here leaves no
    # marker, so the next chain redoes the week.
    async with db.transaction():
        await db.append_event(
            type=CYCLE_COMPLETED_EVENT,
            source_uc=SOURCE_UC,
            source_id=run_id,
            payload={
                "trigger": trigger,
                "run_id": run_id,
                "confrontations": knowledge.confrontations,
                "innovations": knowledge.innovations,
            },
            event_date=today,
        )

    return UC8Result(context, worker_result, post_result, knowledge)
