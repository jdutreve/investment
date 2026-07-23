"""Planner context assembly — Call 1b's contract and the mechanical build of
the PlannerContext the Worker reads (docs/ARCHITECTURE.md "Detailed Planner
Steps" → CALL 1b; docs/TASKS.md Task 4.1 PlannerContext).

Call 1b (LLM) does not re-emit the whole context — it SELECTS from the pool the
baseline + retrieval already fetched: which invariant ids to keep (with a
one-line why in `notes`), which passages, and the framing. `assemble_context`
then builds the PlannerContext mechanically from that selection. Two properties
this buys, both load-bearing:

- **never invent** (docs/TASKS.md inclusion rule v): a selected id that is not
  in the fetched pool references nothing; `unknown_ids` surfaces those so the
  agent retries (Phase-1bis), and `assemble_context` includes only known ids.
- **cheap + faithful**: the large structures (regime, ranking, scenarios) pass
  through untouched from the mechanical baseline, so the model cannot corrupt
  them and no tokens are spent re-serialising them.

`active` (condition holds NOW) is computed here, not asked of the model — it is
a fact about today's market, evaluated against the latest signal readings, and
the Worker uses it to weight what actually applies (docs/TASKS.md
PlannerContext.top_invariants: "`active` lets the Worker weight what applies to
today's market").
"""

import dataclasses
import json
import operator
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from investment.db.seed_data import SIGNAL_ALIASES
from investment.db.sqlite import InvestmentDB
from investment.planner.baseline import Baseline
from investment.planner.retrieval import RetrievalPool

_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}
_REGIME_SIGNAL = "regime"


class ContextSelection(BaseModel):
    """Call 1b's output — the selection only (docs/ARCHITECTURE.md CALL 1b:
    "LLM filters, orders, selects, builds"). Ids must come from the fetched
    pool; `notes` carries the one-line "why included" per invariant and the
    free-text framing."""

    invariant_ids: list[str] = Field(default_factory=list)
    passage_ids: list[str] = Field(default_factory=list)
    notes: str = ""


@dataclasses.dataclass(frozen=True)
class PlannerContext:
    """What the Worker reads (docs/TASKS.md Task 4.1). The mechanical baseline
    fields pass through verbatim; `top_invariants` and `passages` are the Call
    1b selection resolved against the pool, each top_invariant carrying the
    computed `active` flag."""

    regime: dict[str, Any]
    global_liquidity: dict[str, Any]
    ranking: list[dict[str, Any]]
    scenarios: list[dict[str, Any]]
    top_invariants: list[dict[str, Any]]
    recent_proposals: list[dict[str, Any]]
    passages: list[dict[str, Any]]
    notes: str


# -- pure core: the pool the selection is validated against -----------------


def invariant_pool(baseline: Baseline, pool: RetrievalPool) -> dict[str, dict[str, Any]]:
    """The candidate invariants Call 1b may select — the baseline's relevance
    buckets UNION the retrieval hits, keyed by id (docs/TASKS.md ④ + retrieval).
    Baseline rows win a collision: they carry the relevance-bucket provenance."""
    merged: dict[str, dict[str, Any]] = {}
    for inv in pool.invariants:
        merged[str(inv["id"])] = inv
    for inv in baseline.top_invariants:  # baseline last → overwrites, wins
        merged[str(inv["id"])] = inv
    return merged


def passage_pool(pool: RetrievalPool) -> dict[str, dict[str, Any]]:
    return {str(p["id"]): p for p in pool.passages}


def unknown_ids(
    selection: ContextSelection,
    inv_pool: dict[str, dict[str, Any]],
    pas_pool: dict[str, dict[str, Any]],
) -> list[str]:
    """Selected ids absent from the fetched pool — the "never invent" gate
    (docs/TASKS.md inclusion rule v). A non-empty result is what pre.py turns
    into a Phase-1bis ModelRetry; empty means the selection is grounded."""
    missing = [i for i in selection.invariant_ids if i not in inv_pool]
    missing += [p for p in selection.passage_ids if p not in pas_pool]
    return missing


# -- pure core: condition-active-now ----------------------------------------


def condition_active_now(
    condition: list[dict[str, Any]],
    latest: dict[str, dict[str, float | None]],
    regime_type: str | None,
) -> bool:
    """Whether an invariant's condition holds against the LATEST readings —
    predicates ANDed; empty condition ('always') is active by definition
    (docs/ARCHITECTURE.md "ACTIVE — i.condition holds NOW"). A predicate whose
    signal has no current reading makes the whole condition inactive: we cannot
    assert it applies today, and 'unknown' must not read as 'active' (the Worker
    would then weight a lighthouse that may not be lit)."""
    for pred in condition:
        signal, feature, op, value = pred["signal"], pred["feature"], pred["op"], pred["value"]
        if op not in _OPS:
            return False
        if signal == _REGIME_SIGNAL:
            if regime_type is None or not _OPS[op](regime_type, value):
                return False
            continue
        row = latest.get(signal)
        current = row.get(feature) if row else None
        if current is None or not _OPS[op](current, value):
            return False
    return True


# -- async DB layer ---------------------------------------------------------


async def _latest_signals(
    db: InvestmentDB, signals: set[str]
) -> dict[str, dict[str, float | None]]:
    """The latest level/speed/acceleration per referenced signal, resolved
    through SIGNAL_ALIASES to its stored ticker. A signal not in the registry
    or with no rows is simply absent — `condition_active_now` reads that as
    'cannot confirm active'."""
    out: dict[str, dict[str, float | None]] = {}
    for signal in signals:
        ticker = SIGNAL_ALIASES.get(signal)
        if ticker is None:
            continue
        rows = await db.query(
            "SELECT level, speed, acceleration FROM market_data WHERE ticker = :t "
            "ORDER BY ts DESC LIMIT 1",
            t=ticker,
        )
        if rows:
            out[signal] = dict(rows[0])
    return out


async def active_invariant_ids(
    db: InvestmentDB, invariant_ids: list[str], regime_type: str | None
) -> set[str]:
    """Of `invariant_ids`, those whose condition holds NOW. Conditions are read
    fresh (the pool rows carry no `condition` column), the referenced signals'
    latest readings are fetched once, and each condition evaluated against
    them."""
    if not invariant_ids:
        return set()
    placeholders = ",".join(f":i{n}" for n in range(len(invariant_ids)))
    params = {f"i{n}": iid for n, iid in enumerate(invariant_ids)}
    rows = await db.query(
        f"SELECT id, condition FROM invariant WHERE id IN ({placeholders})", **params
    )
    conditions = {str(r["id"]): json.loads(r["condition"]) if r["condition"] else [] for r in rows}

    referenced = {
        p["signal"]
        for cond in conditions.values()
        for p in cond
        if p["signal"] != _REGIME_SIGNAL
    }
    latest = await _latest_signals(db, referenced)

    return {
        iid for iid, cond in conditions.items() if condition_active_now(cond, latest, regime_type)
    }


# -- assembly ---------------------------------------------------------------


def assemble_context(
    baseline: Baseline,
    pool: RetrievalPool,
    selection: ContextSelection,
    active_ids: set[str],
) -> PlannerContext:
    """Build the PlannerContext from the mechanical baseline + the validated
    Call 1b selection. Only pool-known ids are included (unknown ones were
    surfaced by `unknown_ids` for the retry, and are dropped here as a
    belt-and-suspenders on "never invent"); selection ORDER is preserved (Call
    1b's ordering is a judgment we keep)."""
    inv_pool = invariant_pool(baseline, pool)
    pas_pool = passage_pool(pool)
    top_invariants = [
        {**inv_pool[i], "active": i in active_ids} for i in selection.invariant_ids if i in inv_pool
    ]
    passages = [pas_pool[p] for p in selection.passage_ids if p in pas_pool]
    return PlannerContext(
        regime=baseline.regime,
        global_liquidity=baseline.global_liquidity,
        ranking=baseline.ranking,
        scenarios=baseline.scenarios,
        top_invariants=top_invariants,
        recent_proposals=baseline.recent_proposals,
        passages=passages,
        notes=selection.notes,
    )


# -- rendering: the baseline SUMMARY for Call 1a ----------------------------


def render_baseline_summary(baseline: Baseline) -> str:
    """A compact text summary of the baseline for Call 1a — enough to pick THIS
    week's corpus queries and zooms (regime, liquidity, the ranked leaders, the
    biggest scenario shifts, the recent proposal outcomes), not the full data.
    Call 1a's job is the variable margin; it needs the DELTAS, not the tables."""
    regime = baseline.regime
    lines = [
        f"Regime: {regime.get('regime_name', 'unknown')} "
        f"({regime.get('regime_type_id', '?')}), confidence {regime.get('confidence', '?')}"
    ]
    events = regime.get("events") or []
    if events:
        lines.append("  events: " + "; ".join(str(e) for e in events[:5]))

    liq = baseline.global_liquidity
    if liq:
        lines.append(f"Global liquidity: level {liq.get('level')}, speed {liq.get('speed')}")

    if baseline.ranking:
        lines.append("Ranking (top 5):")
        for row in baseline.ranking[:5]:
            lines.append(
                f"  {row.get('rank')}. {row.get('portfolio_id')} "
                f"sortino={row.get('sortino_rolling')} calmar={row.get('calmar_rolling')} "
                f"rec={row.get('recommendation')}"
            )

    shifts = sorted(baseline.scenarios, key=lambda s: abs(s.get("shift", 0.0)), reverse=True)
    moved = [s for s in shifts if abs(s.get("shift", 0.0)) > 0.0][:5]
    if moved:
        lines.append("Biggest scenario shifts:")
        for s in moved:
            lines.append(
                f"  {s.get('strategy_id')}/{s.get('scenario')}: "
                f"{s.get('probability')} ({s.get('shift'):+})"
            )

    if baseline.recent_proposals:
        lines.append("Recent proposals:")
        for p in baseline.recent_proposals:
            outcome = p.get("outcome")
            verdict = json.loads(outcome).get("verdict") if isinstance(outcome, str) else "pending"
            lines.append(
                f"  {p.get('date')} {p.get('proposal_type')} "
                f"{p.get('recommendation')} -> {verdict}"
                + (f" (rejected: {p['rejection_reason']})" if p.get("rejection_reason") else "")
            )

    return "\n".join(lines)
