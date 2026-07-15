"""Scenario probability warm-start + numeric trigger evaluation
(docs/TASKS.md Phase 5bis `scenarios.py`; docs/USE_CASES.md UC0 step 11c;
docs/ARCHITECTURE.md "Unified improvement cycle").

The (future, M8+) weekly mechanical job evaluates NUMERIC triggers only
(grammar `<TICKER|ALIAS> <op> <number>`; unparseable -> Worker-only).
`warm_start_scenario_probabilities()` (UC0 step 11c) reuses the SAME parser
to compute each Scenario's historical weekly hit-rate over the 35y
backfill — the base rate the seed `scenario_probability` is set from, "not
hand-set" (docs/MILESTONES.md M5 DoV).
"""

import json
import operator
import re
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd

from investment.db.sqlite import InvestmentDB

_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "<=": operator.le,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    ">": operator.gt,
}
# Longest-op-first alternation so '<=' matches before a bare '<' would.
_OP_PATTERN = "|".join(sorted((re.escape(op) for op in _OPS), key=len, reverse=True))
_TRIGGER_RE = re.compile(rf"^\s*(\S+)\s*({_OP_PATTERN})\s*(-?\d+(?:\.\d+)?)\s*$")

# TICKER|ALIAS -> resolved market_data ticker — the aliases actually used by
# the seed SCENARIOS' trigger strings (db/seed_data.py).
TRIGGER_ALIASES: dict[str, str] = {
    "CPI_YOY": "CPIAUCSL",
    "GROWTH_COMPOSITE": "GROWTH_COMPOSITE",
    "^VIX": "^VIX",
}

# -- pure core ---------------------------------------------------------


def parse_numeric_trigger(trigger: str) -> tuple[str, str, float] | None:
    """`<TICKER|ALIAS> <op> <number>` -> (resolved_ticker, op, value), or
    `None` if unparseable (Worker-only, per the pinned grammar)."""
    match = _TRIGGER_RE.match(trigger)
    if not match:
        return None
    raw_ticker, op, value = match.group(1), match.group(2), match.group(3)
    return TRIGGER_ALIASES.get(raw_ticker, raw_ticker), op, float(value)


def parse_trigger_conjunction(trigger: str) -> list[tuple[str, str, float]] | None:
    """A single trigger string may itself AND multiple predicates (seed data
    has "CPI_YOY > 4 AND GROWTH_COMPOSITE < 98"); the WHOLE string is
    unparseable (`None`) if any conjunct fails the single-predicate
    grammar."""
    parts = [p.strip() for p in trigger.split(" AND ")]
    parsed = [parse_numeric_trigger(p) for p in parts]
    if any(p is None for p in parsed):
        return None
    return [p for p in parsed if p is not None]


def evaluate_trigger_series(
    conjuncts: list[tuple[str, str, float]], signal_levels: dict[str, pd.Series]
) -> pd.Series:
    """AND every conjunct across every parseable trigger STRING in a
    Scenario's trigger list — judgment call: a scenario's triggers are read
    as jointly necessary conditions ("bull" needs both low inflation AND
    high growth, not either), not alternatives; `signal_levels` must already
    share one common, forward-filled daily index."""
    mask: pd.Series | None = None
    for ticker, op, value in conjuncts:
        column = signal_levels[ticker]
        m = column.notna() & _OPS[op](column, value)
        mask = m if mask is None else (mask & m)
    return mask if mask is not None else pd.Series(dtype=bool)


def evaluate_trigger_availability(
    conjuncts: list[tuple[str, str, float]], signal_levels: dict[str, pd.Series]
) -> pd.Series:
    """Whether EVERY conjunct's ticker has real (non-NaN) data on a given
    day — bounds `_residual_series`'s inference to dates its referenced
    signals actually cover."""
    mask: pd.Series | None = None
    for ticker, _, _ in conjuncts:
        m = signal_levels[ticker].notna()
        mask = m if mask is None else (mask & m)
    return mask if mask is not None else pd.Series(dtype=bool)


def residual_series(
    active_by_name: dict[str, pd.Series], available_by_name: dict[str, pd.Series]
) -> tuple[pd.Series, pd.Series]:
    """A scenario with no parseable trigger of its own (every seeded 'base'
    case uses qualitative triggers, e.g. "Fed pause", or a bare range like
    "CPI_YOY 2.5-3.5" that the single-predicate grammar can't parse) is read
    as "neither of the OTHER scenarios' conditions held" — the complement,
    restricted to dates where those other scenarios' own signals actually
    have data. Without this, a strategy's 'base' rate warm-starts at a flat
    0% for every strategy (verified against the real seed data), which does
    not read as a fair 35y verdict — a named base/default case should hold
    often, not never. Judgment call: no trigger text names 'base' directly,
    so this is inferred, not read off a predicate."""
    union_active: pd.Series | None = None
    union_available: pd.Series | None = None
    for active, available in zip(active_by_name.values(), available_by_name.values(), strict=True):
        union_active = active if union_active is None else (union_active | active)
        union_available = available if union_available is None else (union_available | available)
    if union_active is None or union_available is None:
        return pd.Series(dtype=bool), pd.Series(dtype=bool)
    return ~union_active, union_available


def base_rate(hit_weeks: int, total_weeks: int) -> float:
    return hit_weeks / total_weeks if total_weeks > 0 else 0.0


def normalize_probabilities(
    raw_rates: dict[str, float], fallback: dict[str, float]
) -> dict[str, float]:
    """Scale a Strategy's 3 scenario hit-rates to sum to 100 (matching
    `Scenario.probability`'s own 0-100 convention). Falls back to the
    hand-set seed probabilities if every rate is 0 (e.g. every trigger
    unparseable) rather than writing all-zero probabilities."""
    total = sum(raw_rates.values())
    if total <= 0:
        return dict(fallback)
    return {name: rate / total * 100.0 for name, rate in raw_rates.items()}


# -- async DB layer (writer path — agent-only, ADR-004/ADR-005) ------------


async def _signal_level(db: InvestmentDB, ticker: str) -> pd.Series:
    rows = await db.query(
        "SELECT ts, level FROM market_data WHERE ticker = :t AND level IS NOT NULL ORDER BY ts",
        t=ticker,
    )
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([r["ts"] for r in rows])
    return pd.Series([r["level"] for r in rows], index=idx, dtype=float)


async def warm_start_scenario_probabilities(db: InvestmentDB) -> dict[str, dict[str, float]]:
    """UC0 step 11c (docs/USE_CASES.md) — for each Strategy's 3 scenarios,
    the historical weekly hit-rate of its (parseable) numeric trigger(s) over
    the full 35y backfill, normalized to sum 100, written as the seed
    `scenario_probability` row keyed by `scenario` = the Scenario id (not the
    bare 'bull'/'base'/'bear' name, which collides across strategies).
    Idempotent: `scenario_probability` has a real composite PK, `INSERT OR
    REPLACE` overwrites a same-day rerun."""
    scenarios = await db.query(
        "SELECT id, strategy_id, name, probability, triggers FROM scenario ORDER BY strategy_id, id"
    )
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for sc in scenarios:
        by_strategy.setdefault(str(sc["strategy_id"]), []).append(sc)

    needed_tickers: set[str] = set()
    parsed_by_scenario: dict[str, list[tuple[str, str, float]] | None] = {}
    for sc in scenarios:
        triggers = json.loads(sc["triggers"]) if sc["triggers"] else []
        conjuncts: list[tuple[str, str, float]] = []
        for trigger in triggers:
            parsed = parse_trigger_conjunction(trigger)
            if parsed is not None:
                conjuncts.extend(parsed)
        parsed_by_scenario[str(sc["id"])] = conjuncts or None
        needed_tickers.update(t for t, _, _ in conjuncts)

    signal_levels = {t: await _signal_level(db, t) for t in needed_tickers}
    non_empty = [s for s in signal_levels.values() if not s.empty]
    # Every needed ticker must get an `aligned` entry (even an empty Series)
    # regardless of whether ANY ticker has data — `evaluate_trigger_series`
    # indexes `aligned` by every conjunct's ticker unconditionally.
    calendar = (
        pd.date_range(
            min(s.index.min() for s in non_empty), max(s.index.max() for s in non_empty), freq="D"
        )
        if non_empty
        else pd.DatetimeIndex([])
    )
    aligned = {t: s.reindex(calendar).ffill() for t, s in signal_levels.items()}

    result: dict[str, dict[str, float]] = {}
    for strategy_id, strategy_scenarios in by_strategy.items():
        fallback: dict[str, float] = {}
        active_by_name: dict[str, pd.Series] = {}
        available_by_name: dict[str, pd.Series] = {}
        unparseable_names: list[str] = []
        for sc in strategy_scenarios:
            name = str(sc["name"])
            fallback[name] = float(sc["probability"])
            scenario_conjuncts = parsed_by_scenario[str(sc["id"])]
            if not scenario_conjuncts:
                unparseable_names.append(name)
                continue
            active_by_name[name] = evaluate_trigger_series(scenario_conjuncts, aligned)
            available_by_name[name] = evaluate_trigger_availability(scenario_conjuncts, aligned)

        if len(unparseable_names) == 1 and active_by_name:
            residual_name = unparseable_names[0]
            active_by_name[residual_name], available_by_name[residual_name] = residual_series(
                active_by_name, available_by_name
            )

        raw_rates: dict[str, float] = {}
        for sc in strategy_scenarios:
            name = str(sc["name"])
            if name not in active_by_name:
                raw_rates[name] = 0.0
                continue
            available = available_by_name[name]
            full_active = active_by_name[name]
            active = full_active[available] if not available.empty else full_active
            weekly = active.resample("W-FRI").last().dropna().astype(bool)
            raw_rates[name] = base_rate(int(weekly.sum()), len(weekly))

        probabilities = normalize_probabilities(raw_rates, fallback)
        result[strategy_id] = probabilities
        for sc in strategy_scenarios:
            await db.command(
                "INSERT OR REPLACE INTO scenario_probability "
                "(strategy_id, scenario, ts, probability) VALUES (:sid, :scenario, :ts, :prob)",
                sid=strategy_id,
                scenario=sc["id"],
                ts=date.today().isoformat(),
                prob=probabilities[str(sc["name"])],
            )
    return result
