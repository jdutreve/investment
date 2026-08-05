"""Run the mechanical Monday chain against an as-of-t snapshot (M8b —
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
from investment.mechanical.backtests import run_backtests_and_favors
from investment.mechanical.ratios import value_portfolios
from investment.mechanical.scenarios import warm_start_scenario_probabilities
from investment.mechanical.snapshots import build_snapshot

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


async def run_as_of_cycle(db: InvestmentDB, as_of: date) -> AsOfCycle:
    """Bring `db` (an as-of-t snapshot) to the state the live chain would have
    left it in at `as_of`, ready for the Planner/Worker to read.

    The order is the Monday chain's own and each step feeds the next: FAVORS are
    re-aggregated from the surviving backtests, scenario probabilities from the
    surviving signals, UC6 refills the Portfolio indicators the snapshot blanked,
    and UC7 ranks on those indicators. Running UC7 first would rank on NULLs.
    """
    rows = await db.query("SELECT key, value FROM system_thresholds")
    thresholds = {str(r["key"]): float(r["value"]) for r in rows}
    window = int(thresholds["rolling_window_days"])

    favors = await run_backtests_and_favors(db, window, today=as_of)
    scenarios = await warm_start_scenario_probabilities(db, today=as_of)
    valuations = await value_portfolios(db, window)
    ranked = await build_snapshot(db, thresholds["ranking_tiebreak_window"], as_of)

    cycle = AsOfCycle(
        as_of=as_of,
        favors_edges=favors.favors_edges,
        strategies_scored=len(scenarios),
        portfolios_valued=len(valuations),
        portfolios_ranked=len(ranked),
    )
    logger.info("as-of cycle %s: %s", as_of, cycle)
    return cycle
