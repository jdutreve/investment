"""Run the mechanical weekly chain against an as-of-t snapshot (M8b —
docs/TASKS.md Task 9.4).

`db/as_of_snapshot.py` guarantees that nothing from after t can be READ. It does
not, and cannot, guarantee that what the live agent would have HELD at t exists:
the derived artefacts are dated, so they prune away with everything else. On the
live database at 2008-10-01 the pruned copy has zero `portfolio_weekly_snapshot`
rows and zero `scenario_probability` rows — exactly the two tables the Planner's
baseline reads. Handed that snapshot as-is, the Worker would deliberate on an
empty ranking and no scenarios, and the screen would measure the gap rather than
the agent.

So the snapshot is HYDRATED first, and hydration is nothing but THE LIVE JOBS RUN
AGAINST IT. Not a reimplementation of them — the same four functions the Monday
chain calls, in the chain's own order, reading a database whose inputs stop at t
and therefore producing outputs as-of t. That is the whole reason the agentic
replay reuses one harness (Task 9.4: "so replay logic cannot DRIFT from live
logic — the classic replay bug"), applied one level down: the mechanical half of
the cycle drifts just as easily as the cognitive half.

WHAT IS NOT RE-RUN, and why:
  - NAV — `portfolio_nav` is a trailing series, so the rows that survived the
    prune ARE the as-of NAV. Nothing to recompute.
  - Regime detection — `regime` rows are pruned on the confirming print's date
    and `is_current` is repaired by the snapshot, so the regime known at t is
    already the one the snapshot presents.
Both would be expensive and neither would change an answer.

Measured on the live database (2008-10-01): 0.3s for the four jobs, so the ~20
decision dates of an `episodes` replay cost seconds, not an hour. The cost that
matters in M8b is the LLM calls, and this is not in their league.
"""

import logging
from dataclasses import dataclass
from datetime import date

from investment.db.sqlite import InvestmentDB
from investment.market_signal_cycle import run_market_signal_cycle
from investment.mechanical.backtests import run_backtests_and_favors
from investment.mechanical.invariants import (
    REFERENCE_STATUS,
    compute_weight_update,
    time_validation_verdict,
)
from investment.mechanical.ratios import value_portfolios
from investment.mechanical.scenarios import warm_start_scenario_probabilities
from investment.mechanical.snapshots import build_snapshot
from investment.planner.context import active_invariant_ids

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AsOfCycle:
    """What the mechanical half produced at one decision date — the numbers a
    replay report needs to show the cycle actually ran, not that it was skipped."""

    as_of: date
    favors_edges: int
    strategies_scored: int
    portfolios_valued: int
    portfolios_ranked: int
    invariants_reweighed: int
    market_signal_decision: str | None
    market_signal_proposal_id: str | None


async def run_as_of_cycle(db: InvestmentDB, as_of: date) -> AsOfCycle:
    """Bring `db` (an as-of-t snapshot) to the state the live chain would have
    left it in at `as_of`, ready for the Planner/Worker to read.

    The order is the weekly chain's own and each step feeds the next: FAVORS are
    re-aggregated from the surviving backtests, scenario probabilities from the
    surviving signals, UC6 refills the Portfolio indicators the snapshot blanked,
    and UC7 ranks on those indicators. Running UC7 first would rank on NULLs.
    """
    rows = await db.query("SELECT key, value FROM system_thresholds")
    thresholds = {str(r["key"]): float(r["value"]) for r in rows}
    window = int(thresholds["rolling_window_days"])

    reweighed = await reweigh_invariants_asof(db, as_of, thresholds)
    favors = await run_backtests_and_favors(db, window, today=as_of)
    scenarios = await warm_start_scenario_probabilities(db, today=as_of)
    valuations = await value_portfolios(db, window)
    ranked = await build_snapshot(db, thresholds["ranking_tiebreak_window"], as_of)

    # ADR-007's LIVE allocation, last because the live chain runs it last (08:55,
    # after the ranking and before UC8) and because the Worker READS its decision
    # as context. Without this step the replayed Worker deliberates on a
    # `market_signal: {}` baseline and the screen validates only the retained
    # bridge — the path the pivot superseded. With it, the mechanical decision is
    # journalled BEFORE the Worker speaks, which is the order ADR-011 requires:
    # the Worker may nuance the month, it may not re-pick the book.
    profile = await db.query(
        "SELECT max_drawdown_pct, max_single_asset_pct FROM user_profile LIMIT 1"
    )
    signal = await run_market_signal_cycle(db, dict(profile[0]), today=as_of) if profile else None

    cycle = AsOfCycle(
        as_of=as_of,
        favors_edges=favors.favors_edges,
        strategies_scored=len(scenarios),
        portfolios_valued=len(valuations),
        portfolios_ranked=len(ranked),
        invariants_reweighed=reweighed,
        market_signal_decision=(
            str(signal.decision.date.date()) if signal and signal.decision else None
        ),
        market_signal_proposal_id=signal.proposal_id if signal else None,
    )
    logger.info("as-of cycle %s: %s", as_of, cycle)
    return cycle


async def reweigh_invariants_asof(
    db: InvestmentDB, as_of: date, thresholds: dict[str, float]
) -> int:
    """Re-derive every invariant's weight and verdict from the confrontations
    that survived the prune — the live 08:40 job's arithmetic, on evidence
    bounded at t.

    This is what makes M8b SEMI-PIT rather than merely best-case, and it is only
    possible because the birth maturation dated its confrontations at the
    historical moments it tested (1980-2026) rather than at the seed's wall
    clock. So an invariant's EXISTENCE is today's knowledge (the corpus is not
    pruned — pruning it empties gate 6 and the screen measures nothing) while its
    STANDING is 2008's. Measured on the live DB at 2008-10-01, the four
    integrated invariants carry 27, 29, 20 and 3 confrontations from before that
    date: enough to be judged, and judged differently than in 2026.

    `days_since` is CONDITION-RELATIVE and approximated by the last confrontation
    at or before t (0 when the condition is active now). A confrontation is only
    written at a moment the condition HELD, so its date is when the invariant was
    last live — the same quantity the birth sweep walks the condition series to
    find, without walking it 253 times per decision date. Stated because it is an
    approximation, not a derivation.
    """
    rows = await db.query(
        "SELECT id, weight_initial, floor_weight, status FROM invariant WHERE status != :reference",
        reference=REFERENCE_STATUS,
    )
    if not rows:
        return 0

    counts = {
        str(r["invariant_id"]): r
        for r in await db.query(
            "SELECT invariant_id, "
            " SUM(CASE WHEN verdict = 'confirmed' THEN 1 ELSE 0 END) AS confirmations, "
            " SUM(CASE WHEN verdict = 'refuted' THEN 1 ELSE 0 END) AS infirmations, "
            ' MAX("date") AS last_seen '
            "FROM invariant_confrontations GROUP BY invariant_id"
        )
    }
    regime = await db.query("SELECT regime_type_id FROM regime WHERE is_current = 1")
    regime_type = str(regime[0]["regime_type_id"]) if regime else None
    active = await active_invariant_ids(db, [str(r["id"]) for r in rows], regime_type)

    half_life = thresholds["recency_half_life_days"]
    updated = 0
    for row in rows:
        invariant_id = str(row["id"])
        tallies = counts.get(invariant_id)
        confirmations = int(tallies["confirmations"] or 0) if tallies else 0
        infirmations = int(tallies["infirmations"] or 0) if tallies else 0
        last_seen = str(tallies["last_seen"]) if tallies and tallies["last_seen"] else None
        if invariant_id in active or last_seen is None:
            days_since = 0
        else:
            days_since = max((as_of - date.fromisoformat(last_seen[:10])).days, 0)

        score, recency, weight = compute_weight_update(
            float(row["weight_initial"]),
            float(row["floor_weight"]),
            confirmations,
            infirmations,
            days_since,
            half_life,
        )
        status = time_validation_verdict(
            confirmations,
            infirmations,
            score,
            n_min=thresholds["invariant_min_confrontations"],
            theta=thresholds["invariant_time_validation_score"],
            refuted_min_confrontations=thresholds["invariant_refuted_min_confrontations"],
            refuted_score=thresholds["invariant_refuted_score"],
            verdict_confidence=thresholds["invariant_verdict_confidence"],
            null_score=thresholds["invariant_null_score"],
        )
        await db.command(
            "UPDATE invariant SET confirmation_count = :cc, infirmation_count = :ic, "
            "market_score = :score, recency_factor = :recency, weight_effective = :weff, "
            "status = :status, updated_at = :now WHERE id = :id",
            cc=confirmations,
            ic=infirmations,
            score=score,
            recency=recency,
            weff=weight,
            status=status,
            now=as_of.isoformat(),
            id=invariant_id,
        )
        updated += 1
    return updated
