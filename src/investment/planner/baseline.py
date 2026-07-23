"""Planner mechanical baseline — the 5 fixed context queries, NO LLM
(docs/ARCHITECTURE.md "Detailed Planner Steps" → PYTHON Baseline; docs/TASKS.md
Task 4.1 steps 1-5).

"No judgment involved, so no LLM" (ARCHITECTURE): these five reads are the same
every week. Only the VARIABLE margin (Call 1a's corpus queries + zooms) needs a
model. Splitting the baseline into its own pure-async module — rather than
inlining it in `pre.py` — mirrors the mechanical/ split (a testable core with a
thin DB layer) and lets the baseline be verified against a seeded DB without
standing up the LLM transport.

The one ordering the docs' "asyncio.gather 5 queries" glosses: bucket ④
(relevance-ranked invariants) needs the CURRENT regime (①) and the held assets
(②) to know what "relevant" means, so it runs after that pair — the independent
reads (①②③⑤) still gather concurrently.
"""

import asyncio
import contextlib
import dataclasses
import json
from typing import Any

from investment.db.sqlite import InvestmentDB

# ④ K per bucket and the post-dedup cap (docs/TASKS.md Task 4.1: "K=8 each,
# ≤20 after dedup"). Integrated-only — a proposal may cite only integrated
# invariants (UC8 gate 6), so the Worker is shown the same eligible set.
BUCKET_K = 8
INVARIANTS_CAP = 20
RECENT_PROPOSALS = 3

_INVARIANT_COLS = (
    "id, title, weight_effective, tags, author, status, "
    "confirmation_count, infirmation_count, market_score"
)


@dataclasses.dataclass(frozen=True)
class Baseline:
    """The mechanical context the VARIABLE Planner margin builds on. Every
    field is a plain structure ready to summarise for Call 1a — JSON columns
    (tags, events, aliases, allocation) are already parsed, not raw strings."""

    regime: dict[str, Any]  # current regime instance + its type name/aliases; {} if none
    global_liquidity: dict[str, Any]  # latest GLOBAL_LIQUIDITY level/speed; {} if none
    ranking: list[dict[str, Any]]  # latest snapshot rows, rank ASC
    scenarios: list[dict[str, Any]]  # per (strategy, scenario): latest prob + wow shift
    top_invariants: list[dict[str, Any]]  # 3 relevance buckets, integrated, ≤20 deduped
    recent_proposals: list[dict[str, Any]]  # last 3, any status


# -- pure core --------------------------------------------------------------


def _parse_json_fields(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Parse the named JSON-text columns in place. A NULL or unparseable value
    is left as `[]`/`{}` per the field's shape rather than crashing the whole
    baseline on one malformed row — the summary degrades, it does not abort."""
    out = dict(row)
    for f in fields:
        raw = out.get(f)
        if isinstance(raw, str):
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                out[f] = json.loads(raw)
    return out


def held_assets(ranking: list[dict[str, Any]]) -> set[str]:
    """The tickers the defender + challengers hold (docs/TASKS.md ④: "assets
    held by defender+challengers"). Every ranked portfolio is either the
    defender or a challenger, so the union of their allocation keys IS that
    set. `allocation` is already parsed to a dict here."""
    assets: set[str] = set()
    for row in ranking:
        alloc = row.get("allocation")
        if isinstance(alloc, dict):
            assets.update(str(k) for k in alloc)
    return assets


def dedupe_buckets(*buckets: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Concatenate the relevance buckets in priority order, keeping the FIRST
    occurrence of each invariant id, capped at `cap` (docs/TASKS.md ④: "dedupe
    across buckets ... ≤20"). Bucket order is the priority: an invariant that
    is regime-relevant AND globally heavy is kept in the regime bucket's slot,
    not counted twice."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for bucket in buckets:
        for inv in bucket:
            inv_id = str(inv["id"])
            if inv_id not in seen:
                seen.add(inv_id)
                merged.append(inv)
                if len(merged) >= cap:
                    return merged
    return merged


def _asset_tag_predicate(assets: set[str]) -> tuple[str, dict[str, Any]]:
    """A parameterised `(sql, params)` matching any invariant whose tags name
    one of the held assets (`asset:<ticker>`). Empty assets → a never-true
    predicate, so the asset bucket is simply empty rather than matching all."""
    if not assets:
        return "0", {}
    clauses = []
    params: dict[str, Any] = {}
    for i, asset in enumerate(sorted(assets)):
        key = f"a{i}"
        clauses.append(f"tags LIKE :{key}")
        params[key] = f'%"asset:{asset}"%'
    return "(" + " OR ".join(clauses) + ")", params


# -- async DB layer ---------------------------------------------------------


async def _regime(db: InvestmentDB) -> dict[str, Any]:
    rows = await db.query(
        "SELECT r.regime_type_id, r.tags, r.confidence, r.is_current, r.events, "
        "r.start_date, rt.name AS regime_name, rt.aliases "
        "FROM regime r JOIN regime_type rt ON rt.id = r.regime_type_id "
        "WHERE r.is_current = 1 LIMIT 1"
    )
    if not rows:
        return {}
    return _parse_json_fields(rows[0], ("tags", "events", "aliases"))


async def _global_liquidity(db: InvestmentDB) -> dict[str, Any]:
    rows = await db.query(
        "SELECT ts, level, speed FROM market_data WHERE ticker = 'GLOBAL_LIQUIDITY' "
        "ORDER BY ts DESC LIMIT 1"
    )
    return dict(rows[0]) if rows else {}


async def _ranking(db: InvestmentDB) -> list[dict[str, Any]]:
    rows = await db.query(
        "SELECT * FROM portfolio_weekly_snapshot "
        "WHERE date = (SELECT MAX(date) FROM portfolio_weekly_snapshot) "
        "ORDER BY rank ASC"
    )
    return [_parse_json_fields(r, ("allocation",)) for r in rows]


async def _scenarios(db: InvestmentDB) -> list[dict[str, Any]]:
    """Latest probability per (strategy, scenario) with the week-over-week
    shift (docs/TASKS.md ③: "LAG on scenario_probability"). `shift` is 0.0 on
    the first ever print (no prior week to differ from) — COALESCE to the
    current value makes the difference exactly zero, not NULL."""
    rows = await db.query(
        "WITH ranked AS ("
        "  SELECT strategy_id, scenario, ts, probability,"
        "         LAG(probability) OVER "
        "           (PARTITION BY strategy_id, scenario ORDER BY ts) AS prev_prob,"
        "         ROW_NUMBER() OVER "
        "           (PARTITION BY strategy_id, scenario ORDER BY ts DESC) AS rn"
        "  FROM scenario_probability"
        ") "
        "SELECT strategy_id, scenario, ts, probability, "
        "       probability - COALESCE(prev_prob, probability) AS shift "
        "FROM ranked WHERE rn = 1 "
        "ORDER BY strategy_id, scenario"
    )
    return [dict(r) for r in rows]


async def _recent_proposals(db: InvestmentDB) -> list[dict[str, Any]]:
    rows = await db.query(
        "SELECT * FROM proposal ORDER BY date DESC, created_at DESC LIMIT :n", n=RECENT_PROPOSALS
    )
    return [_parse_json_fields(r, ("proposed_allocation",)) for r in rows]


async def _bucket(db: InvestmentDB, where: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = await db.query(
        f"SELECT {_INVARIANT_COLS} FROM invariant "
        f"WHERE status = 'integrated' AND {where} "
        f"ORDER BY weight_effective DESC LIMIT {BUCKET_K}",
        **params,
    )
    return [_parse_json_fields(r, ("tags",)) for r in rows]


async def _top_invariants(
    db: InvestmentDB, regime: dict[str, Any], ranking: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The 3 relevance buckets, integrated-only, deduped and capped
    (docs/TASKS.md ④). Regime-tag bucket first (most contextual), then held
    assets, then global weight — "weight alone would surface the same Dalio
    heavyweights forever, regime-blind", so it is the LAST resort, not the
    first."""
    regime_type_id = regime.get("regime_type_id")
    regime_bucket: list[dict[str, Any]] = []
    if regime_type_id:
        regime_bucket = await _bucket(
            db, "tags LIKE :pat", {"pat": f'%"regime:{regime_type_id}"%'}
        )

    asset_where, asset_params = _asset_tag_predicate(held_assets(ranking))
    asset_bucket = await _bucket(db, asset_where, asset_params)

    global_bucket = await _bucket(db, "1 = 1", {})

    return dedupe_buckets(regime_bucket, asset_bucket, global_bucket, cap=INVARIANTS_CAP)


async def gather_baseline(db: InvestmentDB) -> Baseline:
    """The 5 mechanical baseline queries (docs/ARCHITECTURE.md "Detailed
    Planner Steps"). The independent reads gather concurrently; bucket ④ then
    runs against the resolved regime + ranking (see module docstring)."""
    regime, global_liquidity, ranking, scenarios, recent_proposals = await asyncio.gather(
        _regime(db),
        _global_liquidity(db),
        _ranking(db),
        _scenarios(db),
        _recent_proposals(db),
    )
    top_invariants = await _top_invariants(db, regime, ranking)
    return Baseline(
        regime=regime,
        global_liquidity=global_liquidity,
        ranking=ranking,
        scenarios=scenarios,
        top_invariants=top_invariants,
        recent_proposals=recent_proposals,
    )
