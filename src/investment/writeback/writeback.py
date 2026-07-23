"""Writeback — the pure executor + mechanical proposal gates (docs/TASKS.md
Phase 6; docs/USE_CASES.md UC8). "Worker proposes, Writeback disposes"
(CLAUDE.md): the Worker's reallocation is validated by DETERMINISTIC gates here
and only then persisted, EventLog-first (CLAUDE.md "EventLog" rule).

This slice is the reallocation disposition — the path M8's Definition of
Verified exercises ("bear-shift fixture → reallocation proposal passes gates").
It adds the two gates the mechanical replay could not supply before M8
(mechanical/gates.py module docstring): gate 6 (cited-invariant eligibility,
now that a real Worker cites invariants) and `effective_caps` (the stricter-of
user/portfolio caps). The switch disposition and the knowledge/innovation commit
are the following increments.
"""

import json
from datetime import date
from typing import Any

from ulid import ULID

from investment.db.sqlite import InvestmentDB
from investment.mechanical.gates import (
    Caps,
    GateOutcome,
    ProposalThresholds,
    cited_invariant_eligible,
    reallocation_gates,
)
from investment.planner.context import active_invariant_ids
from investment.worker.result import ReallocationProposal

PROPOSAL_EVENT = "ProposalEvent"
SOURCE_UC = "UC8"


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
