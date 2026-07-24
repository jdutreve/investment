"""Writeback — the pure executor + mechanical proposal gates (docs/TASKS.md
Phase 6; docs/USE_CASES.md UC8). "Worker proposes, Writeback disposes"
(CLAUDE.md): the Worker's reallocation is validated by DETERMINISTIC gates here
and only then persisted, EventLog-first (CLAUDE.md "EventLog" rule).

Two responsibilities: the reallocation DISPOSITION (gates 1-6, then commit the
Proposal EventLog-first) and the knowledge COMMIT (`commit_knowledge`) — the
guardrailed PostPlannerResult persisted to the graph: source='evaluation'
confrontations (weight-moving, condition-gated), conviction nudges, and coherent
scenario-probability updates. gate 6 (cited-invariant eligibility) and
`effective_caps` (stricter-of user/portfolio caps) are the two gates the
mechanical replay could not supply before M8 (mechanical/gates.py docstring).
The switch disposition and the innovation commit (dedup + maturation) are the
remaining increments.
"""

import dataclasses
import json
import logging
from datetime import UTC, date, datetime
from typing import Any

from ulid import ULID

from investment.corpus.embedding import Embedder, invariant_embedding_input, to_blob
from investment.db.sqlite import InvestmentDB
from investment.mechanical.gates import (
    Caps,
    GateOutcome,
    ProposalThresholds,
    cited_invariant_eligible,
    reallocation_gates,
)
from investment.mechanical.invariants import compute_weight_update, mature_seed_invariants
from investment.planner.context import active_invariant_ids
from investment.planner.post import PostPlannerResult
from investment.worker.result import ImprovementProposal, ReallocationProposal
from investment.writeback.knowledge import (
    DEDUP_COSINE_THRESHOLD,
    find_duplicate,
    load_invariant_corpus,
)

logger = logging.getLogger(__name__)

PROPOSAL_EVENT = "ProposalEvent"
CONFRONTATION_EVENT = "ConfrontationEvent"
EVALUATION_EVENT = "EvaluationEvent"
SCENARIO_EVENT = "ScenarioEvent"
INNOVATION_EVENT = "InnovationEvent"
SOURCE_UC = "UC8"
_SCENARIO_KINDS = frozenset({"bull", "base", "bear"})
_SCENARIO_SUM_TOLERANCE = 0.1
_STRATEGY_INNOVATION_TYPES = frozenset({"new_strategy", "strategy_revision"})


def effective_caps(user_profile: dict[str, Any], portfolio: dict[str, Any] | None) -> Caps:
    """The BINDING caps for a proposal: the STRICTER of the user_profile and the
    portfolio's own rule (CLAUDE.md "Binding caps": per-portfolio rules may only
    be stricter). Both drawdown limits are negative, so stricter is the LARGER
    (less-negative) — `max`; for the single-asset cap stricter is the SMALLER —
    `min`. A portfolio without its own rule inherits the user cap unchanged."""
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


async def _allowed_reallocation_tickers(db: InvestmentDB) -> frozenset[str]:
    """The tickers a reallocation may hold (docs/TASKS.md Phase 6 gate B): active
    tradable tickers (non-MACRO asset class) plus the synthetic 'cash' sleeve."""
    rows = await db.query(
        "SELECT ticker FROM allowed_tickers WHERE active = 1 AND asset_class != 'MACRO'"
    )
    return frozenset({str(r["ticker"]) for r in rows} | {"cash"})


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
            return GateOutcome.refused("gate6_cited_invariant_eligibility")
    return GateOutcome(passed=True)


async def _commit_reallocation(
    db: InvestmentDB,
    defender_id: str,
    reallocation: ReallocationProposal,
    current: dict[str, float],
    market_context: dict[str, Any],
    today: date,
) -> str:
    """Persist a passing reallocation: ProposalEvent to the EventLog FIRST, then
    the Proposal vertex, then upgrade the defender's latest snapshot
    recommendation — all in ONE transaction (CLAUDE.md "EventLog"). `outcome` is
    left NULL: evaluate_proposals() picks it up as pending at +12w."""
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
            "recommendation, gap, market_context, reasoning, trace, created_at) VALUES "
            "(:id, :date, 'reallocation', :defender, :alloc, 'paper-test', :gap, :ctx, :reason, "
            ":trace, :now)",
            id=proposal_id,
            date=today.isoformat(),
            defender=defender_id,
            alloc=json.dumps(reallocation.proposed_allocation),
            gap=json.dumps({"allocation_diff": allocation_diff}),
            ctx=json.dumps(market_context),
            reason=reallocation.reasoning,
            trace=trace,
            now=today.isoformat(),
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
        # Upgrade the defender's latest snapshot recommendation so the digest
        # reflects that a paper-test was emitted (no-op if no snapshot yet).
        await db.command(
            "UPDATE portfolio_weekly_snapshot SET recommendation = 'paper-test' "
            "WHERE portfolio_id = :pid AND date = "
            "(SELECT MAX(date) FROM portfolio_weekly_snapshot WHERE portfolio_id = :pid)",
            pid=defender_id,
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
    """Run the reallocation gates (UC8-B 1-5, then gate 6) and, on pass, commit
    the Proposal. Returns `(outcome, proposal_id)` — `proposal_id` is None on a
    block (no vertex; the caller sends the ⛔ Telegram note with the failed
    gate). "Worker proposes, Writeback disposes" — nothing is persisted until
    every gate passes."""
    today = today or date.today()
    caps = effective_caps(user_profile, portfolio)
    thr = proposal_thresholds(thresholds)
    allowed = await _allowed_reallocation_tickers(db)

    outcome = reallocation_gates(
        current_allocation, reallocation.proposed_allocation, caps, thr, allowed
    )
    if not outcome.passed:
        return outcome, None

    gate6 = await gate6_cited_invariants(
        db, reallocation.supporting_invariants, thresholds, regime_type
    )
    if not gate6.passed:
        return gate6, None

    proposal_id = await _commit_reallocation(
        db, defender_id, reallocation, current_allocation, market_context, today
    )
    return GateOutcome(passed=True), proposal_id


# -- knowledge commit (PostPlannerResult -> graph) --------------------------


@dataclasses.dataclass(frozen=True)
class KnowledgeCommit:
    """What the knowledge commit persisted this cycle. Innovations are the next
    increment (dedup + maturation), so they are reported as 0 here."""

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
    """Record the evaluations as an EvaluationEvent and apply each
    conviction_delta to its strategy (clamped 0-100). The verdict itself
    matures MECHANICALLY at +12w (outcomes.py) — this only nudges the Worker's
    running conviction, it does not adopt/reject anything (ADR-006)."""
    if not post_result.evaluations:
        return 0
    now = datetime.now(UTC).isoformat()
    committed = 0
    async with db.transaction():
        await db.append_event(
            type=EVALUATION_EVENT,
            source_uc=SOURCE_UC,
            source_id=None,
            payload={
                "evaluations": [
                    {"strategy_id": e.strategy_id, "verdict": e.verdict}
                    for e in post_result.evaluations
                ]
            },
            event_date=today,
        )
        for ev in post_result.evaluations:
            if ev.conviction_delta == 0.0:
                continue
            await db.command(
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
        rows = await db.query(
            "SELECT id, name FROM scenario WHERE strategy_id = :s", s=sid
        )
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


async def _commit_strategy_innovation(
    db: InvestmentDB, proposal: ImprovementProposal, today: date
) -> str:
    """Create a proposed Strategy vertex from a new_strategy / strategy_revision
    innovation (docs/ARCHITECTURE.md "System Evolution"; docs/TASKS.md Phase 6).
    Born `status='proposed'`, `enabled=false` — it enters mechanical probation
    (strategy_probation_check) and auto-activates on PASS; nothing is enabled by
    the mere proposal (ADR-006). A revision records its lineage in `trace`; the
    superseded vertex is closed only on probation PASS, not here. Returns the new
    strategy id."""
    spec = proposal.spec or {}
    strategy_id = str(spec.get("id") or f"strat-{ULID()}")
    now = datetime.now(UTC).isoformat()
    supersedes = spec.get("supersedes")
    trace = proposal.trace + (f" [supersedes {supersedes}]" if supersedes else "")
    async with db.transaction():
        await db.append_event(
            type=INNOVATION_EVENT,
            source_uc=SOURCE_UC,
            source_id=strategy_id,
            payload={"type": proposal.type, "title": proposal.title, "supersedes": supersedes},
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
            rt=spec.get("regime_type_id"),
            fw=spec.get("framework_id", "4seasons"),
            conv=float(spec.get("conviction", 50.0)),
            cond=str(spec.get("conditions", "")),
            today=today.isoformat(),
            trace=trace,
            now=now,
        )
    return strategy_id


async def _commit_invariant_innovation(
    db: InvestmentDB,
    proposal: ImprovementProposal,
    embedder: Embedder,
    corpus: list[Any],
    matrix: Any,
    today: date,
) -> str | None:
    """Persist a new_invariant innovation through the SHARED dedup gate
    (writeback/knowledge.py `find_duplicate`) — the SAME gate the curator uses,
    so a Worker-proposed invariant and a curator-extracted one dedup against the
    corpus identically. On a duplicate the invariant is NOT re-created (an
    InnovationEvent records the merge target); otherwise it is born
    status='proposed' and matured over 35y by the caller. Returns the new id, or
    None when merged."""
    spec = proposal.spec or {}
    condition = spec.get("condition", [])
    effect = spec.get("effect")
    title, description = proposal.title, proposal.rationale
    vector = embedder.encode([invariant_embedding_input(title, description)])[0]

    match = find_duplicate(
        vector, condition, effect, corpus, matrix, DEDUP_COSINE_THRESHOLD, label=title[:60]
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
                "weight_initial": proposal.weight_initial,
                "floor_weight": proposal.floor_weight,
                "trace": proposal.trace or "UC8 agent-discovery innovation",
            },
        )
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
    corpus: list[Any] = []
    matrix: Any = None
    corpus_loaded = False
    created_invariant = False

    for innovation in post_result.innovations:
        if innovation.type in _STRATEGY_INNOVATION_TYPES:
            await _commit_strategy_innovation(db, innovation, today)
            committed += 1
        elif innovation.type == "new_invariant" and embedder is not None:
            if not corpus_loaded:
                corpus, matrix = await load_invariant_corpus(db)
                corpus_loaded = True
            new_id = await _commit_invariant_innovation(
                db, innovation, embedder, corpus, matrix, today
            )
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
    Phase 6). The guardrail already dropped every unknown id and every
    unevidenced verdict, so this is pure mechanical persistence: source=
    'evaluation' confrontations (weight-moving, condition-gated) and the
    evaluation record + conviction nudges. Scenario updates (which need the
    bull/base/bear -> scenario-id resolution) and innovations (dedup +
    maturation) are the following increments."""
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
