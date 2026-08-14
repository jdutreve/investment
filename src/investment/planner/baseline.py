"""Planner mechanical baseline — the fixed context queries, NO LLM
(docs/ARCHITECTURE.md "Detailed Planner Steps" → PYTHON Baseline; docs/TASKS.md
Task 4.1 steps 1-5, plus ⑥ the live market-signal decision ADR-007 made the
allocation path).

"No judgment involved, so no LLM" (ARCHITECTURE): these reads are the same
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
from typing import Any, cast

from investment.db.sqlite import InvestmentDB
from investment.mechanical.rule_revision import measured_verdicts

# ④ K per bucket and the post-dedup cap (docs/TASKS.md Task 4.1: "K=8 each,
# ≤20 after dedup"). Integrated-only — the filter OUTLIVED its original reason
# and keeps a better one. It was "a proposal may cite only integrated
# invariants (UC8 gate 6), so show the Worker the eligible set"; ADR-012 deleted
# that gate, and the Worker cites nothing structurally any more. What remains is
# the M8 measurement the gate was calibrated on: widening to high-weight
# 'proposed' takes the set from 2 to 218 of 253, because `weight_effective` is
# dominated by the author-tier FLOOR rather than by evidence. That would hand
# the reading 209 curator notes of unmeasured belief, which is a worse context,
# not a richer one — so the Worker still reads the corpus's SETTLED knowledge.
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
    scenarios: list[dict[str, Any]]  # per (strategy, scenario id + name): prob + wow shift
    top_invariants: list[dict[str, Any]]  # 3 relevance buckets, integrated, ≤20 deduped
    recent_proposals: list[dict[str, Any]]  # last 3, any status
    market_signal: dict[str, Any]  # latest live market-signal decision (ADR-007); {} if none
    # Default-empty because absent IS empty and both callers can hit it: no
    # current regime means no per-regime standings to show, and a database with
    # no MACRO rows is a legitimate early state rather than an error.
    favors: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    macro: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    # Knob changes already replayed over 35 years, with their verdicts. The
    # Worker is told them so it stops re-proposing what the history refused —
    # 'add VCIT to the overlay' arrived three times, each after a rejection
    # nothing could show it (rule_revision `measured_verdicts`).
    measured_revisions: list[dict[str, Any]] = dataclasses.field(default_factory=list)


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


async def _favors(db: InvestmentDB, regime_type_id: str | None) -> list[dict[str, Any]]:
    """Every strategy's FAVORS standing in the CURRENT regime type.

    The single biggest thing the Worker was buying with its own tool calls:
    across the two M8b runs it queried this table 37 times, more than anything
    else, because "which strategy suits this regime" is central to its job and
    the table was absent from its context entirely.

    Scoped to the current regime type, not the whole table. FAVORS is per-regime
    precisely so a Sortino earned in stagflation is not compared against a
    disinflation peer group (`outcomes._fallback_regime` makes the same point),
    and handing it all five would invite exactly that comparison."""
    if not regime_type_id:
        return []
    rows = await db.query(
        "SELECT strategy_id, sharpe_rolling, sortino_rolling, calmar_rolling, max_drawdown, "
        "n_periods FROM favors WHERE regime_type_id = :rt ORDER BY sortino_rolling DESC",
        rt=regime_type_id,
    )
    return [dict(r) for r in rows]


async def _macro(db: InvestmentDB) -> list[dict[str, Any]]:
    """The latest level/speed/acceleration of every MACRO series.

    Fetched 15 times across the two M8b runs, one ticker list at a time. The
    Worker reads WEATHER (docs/ARCHITECTURE.md WORKER persona) and its context
    carried exactly two macro readings — the regime label and global liquidity —
    so CPI, the curve, the credit spread and the real rate all cost it tool
    calls it could have spent on portfolios and tickers instead.

    Latest row per ticker, so a series that stopped updating shows its last
    knowable print rather than disappearing (ADR-003: a stale print IS what was
    knowable; `alerts.signal_freshness_alert` is what reports the staleness)."""
    rows = await db.query(
        "SELECT m.ticker, m.ts, m.level, m.speed, m.acceleration FROM market_data m "
        "JOIN (SELECT ticker, MAX(ts) AS ts FROM market_data WHERE asset_class = 'MACRO' "
        "      GROUP BY ticker) last ON last.ticker = m.ticker AND last.ts = m.ts "
        "ORDER BY m.ticker"
    )
    return [dict(r) for r in rows]


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
    current value makes the difference exactly zero, not NULL.

    `scenario` is the Scenario ID (mechanical/scenarios.py: the bare
    'bull'/'base'/'bear' collides across strategies), so `name` is joined in
    alongside it: the LLM boundary speaks NAMES — the Worker reads them, Call 2
    returns them, and `writeback._commit_scenario_updates` resolves name -> id
    to commit. Without the name in the context, every scenario update the Worker
    made would name an id and die on that resolution."""
    rows = await db.query(
        "WITH ranked AS ("
        "  SELECT strategy_id, scenario, ts, probability,"
        "         LAG(probability) OVER "
        "           (PARTITION BY strategy_id, scenario ORDER BY ts) AS prev_prob,"
        "         ROW_NUMBER() OVER "
        "           (PARTITION BY strategy_id, scenario ORDER BY ts DESC) AS rn"
        "  FROM scenario_probability"
        ") "
        "SELECT r.strategy_id, r.scenario, s.name, r.ts, r.probability, "
        "       r.probability - COALESCE(r.prev_prob, r.probability) AS shift "
        "FROM ranked r LEFT JOIN scenario s ON s.id = r.scenario "
        "WHERE r.rn = 1 "
        "ORDER BY r.strategy_id, r.scenario"
    )
    return [dict(r) for r in rows]


async def _recent_proposals(db: InvestmentDB) -> list[dict[str, Any]]:
    rows = await db.query(
        "SELECT * FROM proposal ORDER BY date DESC, created_at DESC LIMIT :n", n=RECENT_PROPOSALS
    )
    return [_parse_json_fields(r, ("proposed_allocation",)) for r in rows]


async def _market_signal(db: InvestmentDB) -> dict[str, Any]:
    """The most recent LIVE market-signal decision (ADR-007), read off its
    EventLog journal — whose monotonic ULID id is the append order, so
    `ORDER BY id DESC LIMIT 1` is the latest by construction.

    The journal, not the `proposal` table: most months hold the same book and
    emit no proposal, and the Worker still needs to know which book is in force
    and how close the signal is to flipping. `{}` before the first decision."""
    rows = await db.query(
        "SELECT payload FROM event_log WHERE type = 'MarketSignalDecisionEvent' "
        "ORDER BY id DESC LIMIT 1"
    )
    if not rows:
        return {}
    with contextlib.suppress(json.JSONDecodeError, ValueError):
        parsed = json.loads(str(rows[0]["payload"]))
        if isinstance(parsed, dict):
            return parsed
    return {}


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
        regime_bucket = await _bucket(db, "tags LIKE :pat", {"pat": f'%"regime:{regime_type_id}"%'})

    asset_where, asset_params = _asset_tag_predicate(held_assets(ranking))
    asset_bucket = await _bucket(db, asset_where, asset_params)

    global_bucket = await _bucket(db, "1 = 1", {})

    return dedupe_buckets(regime_bucket, asset_bucket, global_bucket, cap=INVARIANTS_CAP)


async def gather_baseline(db: InvestmentDB) -> Baseline:
    """The mechanical baseline queries (docs/ARCHITECTURE.md "Detailed Planner
    Steps") — the spec's 5, plus the live market-signal decision ADR-007 made
    the allocation path. The independent reads gather concurrently; bucket ④
    then runs against the resolved regime + ranking (see module docstring)."""
    results = await asyncio.gather(
        _regime(db),
        _global_liquidity(db),
        _ranking(db),
        _scenarios(db),
        _recent_proposals(db),
        _market_signal(db),
        _macro(db),
    )
    # Unpacked after the gather rather than in the assignment: mypy widens a
    # heterogeneous `gather` to the join of its element types, so the tuple form
    # types every field as that join and every downstream use errors.
    regime = cast(dict[str, Any], results[0])
    global_liquidity = cast(dict[str, Any], results[1])
    ranking = cast(list[dict[str, Any]], results[2])
    scenarios = cast(list[dict[str, Any]], results[3])
    recent_proposals = cast(list[dict[str, Any]], results[4])
    market_signal = cast(dict[str, Any], results[5])
    macro = cast(list[dict[str, Any]], results[6])
    # FAVORS needs the resolved regime type, so it runs after the gather — the
    # same sequencing bucket ④ has, and for the same reason.
    top_invariants, favors = await asyncio.gather(
        _top_invariants(db, regime, ranking),
        _favors(db, regime.get("regime_type_id")),
    )
    return Baseline(
        regime=regime,
        global_liquidity=global_liquidity,
        ranking=ranking,
        scenarios=scenarios,
        top_invariants=top_invariants,
        recent_proposals=recent_proposals,
        market_signal=market_signal,
        favors=favors,
        macro=macro,
        measured_revisions=await measured_verdicts(db),
    )
