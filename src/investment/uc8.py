"""UC8 — the decision cycle (docs/USE_CASES.md UC8; docs/ARCHITECTURE.md
"09:00 UC8: Planner Pre → Worker → Planner Post → Writeback"). The first full
cognitive chain: it wires the three strictly-separated roles built in the
earlier M8 slices into one call.

  PlannerPre.run        → PlannerContext (baseline + Call 1a margin + Call 1b)
  Worker                → WorkerResult (interprets, proposes)
  PlannerPost.run       → PostPlannerResult (extract + guardrail)
  Writeback.dispose     → Proposal, only if every gate passes

The Worker is handed the context as TEXT and stays unaware of the Planner,
Writeback and storage (docs/ARCHITECTURE.md WORKER) — `render_context_for_worker`
is that boundary. Writeback runs ONLY on what the Worker proposed: no
reallocation, no gate run, no vertex. Knowledge commit (evaluations / scenarios
/ innovations from the PostPlannerResult) is the following increment; this slice
delivers the reallocation decision path M8's Definition of Verified exercises
end to end.
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
from investment.writeback.writeback import dispose_reallocation


@dataclasses.dataclass(frozen=True)
class UC8Result:
    """One decision cycle's full output — the context the Worker saw, its
    result, the guardrailed knowledge, and the disposition of any reallocation
    (proposal_id set iff every gate passed)."""

    context: PlannerContext
    worker_result: WorkerResult
    post_result: PostPlannerResult
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
        lines.append(
            f"  {sc.get('strategy_id')}/{sc.get('scenario')}: "
            f"{sc.get('probability')} ({sc.get('shift', 0.0):+})"
        )
    lines.append("")
    lines.append("INVARIANTS (lighthouses — [ACTIVE] holds now):")
    for inv in context.top_invariants:
        flag = "[ACTIVE]" if inv.get("active") else "[dormant]"
        lines.append(
            f"  {flag} {inv.get('id')} — {inv.get('title', '')} "
            f"(weight {inv.get('weight_effective', '?')}, {inv.get('author', 'null')})"
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
    context, _tools = await planner_pre.run(trigger)
    worker_result = await run_worker(worker_agent, render_context_for_worker(context))
    post_result = await planner_post.run(worker_result, context)

    gate_outcome: GateOutcome | None = None
    proposal_id: str | None = None
    reallocation = worker_result.reallocation_proposed
    defender = _defender_row(context.ranking)
    if reallocation is not None and defender is not None:
        gate_outcome, proposal_id = await dispose_reallocation(
            db,
            reallocation,
            str(defender["portfolio_id"]),
            _allocation(defender),
            user_profile,
            thresholds,
            context.regime.get("regime_type_id"),
            _market_context(context),
            today=today,
        )

    return UC8Result(context, worker_result, post_result, gate_outcome, proposal_id)
