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
proposal or not — a quiet week still learns.
"""

import dataclasses
import json
from datetime import date
from typing import Any

from pydantic_ai import Agent

from investment.db.sqlite import InvestmentDB
from investment.mechanical.gates import GateOutcome
from investment.planner.context import PlannerContext
from investment.planner.post import PlannerPost, PostPlannerResult
from investment.planner.pre import PlannerPre
from investment.worker.agent import run_worker
from investment.worker.result import WorkerResult
from investment.writeback.writeback import (
    KnowledgeCommit,
    commit_knowledge,
    dispose_reallocation,
    portfolio_caps,
)


@dataclasses.dataclass(frozen=True)
class UC8Result:
    """One decision cycle's full output — the context the Worker saw, its
    result, the guardrailed knowledge, and the disposition of any reallocation
    (proposal_id set iff every gate passed)."""

    context: PlannerContext
    worker_result: WorkerResult
    post_result: PostPlannerResult
    knowledge: KnowledgeCommit  # what the guardrailed knowledge commit persisted
    gate_outcome: GateOutcome | None  # None iff the Worker proposed no reallocation
    proposal_id: str | None


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
    lines.append(
        f"  200d trend overlay: {', '.join(below)} below trend, redirected to the haven"
        if below
        else "  200d trend overlay: no sleeve below trend"
    )
    if hysteresis.get("pending_book"):
        lines.append(
            f"  pending switch to {hysteresis['pending_book']}: "
            f"{hysteresis.get('pending_count')}/{hysteresis.get('confirm_decisions')} confirmations"
        )
    return lines


def render_context_for_worker(context: PlannerContext) -> str:
    """The PlannerContext as the text the Worker reads (docs/ARCHITECTURE.md
    WORKER: "the data in your context"). Deliberately unaware of provenance —
    no Planner, no pool, no storage — just the market picture, the ranking, the
    lighthouses (with which are lit NOW), and the framing."""
    regime = context.regime
    lines = [
        f"REGIME: {regime.get('regime_name', '?')} ({regime.get('regime_type_id', '?')}), "
        f"confidence {regime.get('confidence', '?')}",
        f"GLOBAL LIQUIDITY: {context.global_liquidity}",
    ]
    lines.extend(_market_signal_lines(context.market_signal))
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
    lines.append("")
    lines.append("INVARIANTS (lighthouses — [ACTIVE] holds now):")
    for inv in context.top_invariants:
        flag = "[ACTIVE]" if inv.get("active") else "[dormant]"
        lines.append(
            f"  {flag} {inv.get('id')} — {inv.get('title', '')} "
            f"(weight {inv.get('weight_effective', '?')}, {inv.get('author', 'null')}, "
            f"{inv.get('status', '?')})"
        )
    # The Worker cannot see the gates, but it CAN be told the citation rule —
    # otherwise it leans on a dormant or non-integrated lighthouse and the whole
    # reallocation dies on UC8-B gate 6 for a reason it had no way to know
    # (docs/MILESTONES.md M8 "gate 6 may be near-unsatisfiable").
    if context.top_invariants:
        lines.append(
            "  → to support a reallocation, cite ONLY invariants marked [ACTIVE] and "
            "status 'integrated': a dormant lighthouse describes a market that is not "
            "here, and an unproven one is not yet evidence."
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
) -> UC8Result:
    """Run one UC8 cycle end to end. Writeback only runs if the Worker proposed
    a reallocation AND a defender exists to reallocate; otherwise the cycle is
    knowledge-only (gate_outcome / proposal_id stay None). Returns everything
    the digest renders."""
    context = await planner_pre.run(trigger)
    worker_result = await run_worker(worker_agent, render_context_for_worker(context))
    post_result = await planner_post.run(worker_result, context)

    regime_type = context.regime.get("regime_type_id")
    knowledge = await commit_knowledge(
        db, post_result, regime_type, thresholds, today=today, embedder=planner_pre.embedder
    )

    gate_outcome: GateOutcome | None = None
    proposal_id: str | None = None
    reallocation = worker_result.reallocation_proposed
    defender = _defender_row(context.ranking)
    if reallocation is not None and defender is not None:
        defender_id = str(defender["portfolio_id"])
        # The defender's own caps: the ranking snapshot carries no cap columns
        # (they live on `portfolio`), so without this only the looser user caps
        # would bind — and per-portfolio rules may only be STRICTER.
        portfolio = await portfolio_caps(db, defender_id)
        gate_outcome, proposal_id = await dispose_reallocation(
            db,
            reallocation,
            defender_id,
            _allocation(defender),
            user_profile,
            thresholds,
            regime_type,
            _market_context(context),
            portfolio=portfolio,
            today=today,
        )

    return UC8Result(context, worker_result, post_result, knowledge, gate_outcome, proposal_id)
