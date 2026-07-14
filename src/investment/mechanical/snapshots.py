"""Portfolio ranking (docs/TASKS.md Phase 5bis `snapshots.py`, UC7) — the
ranking rule pinned in docs/DATA_MODELS.md "Ranking rule" / CLAUDE.md
"Ranking rule", applied to every enabled Portfolio (defender included, never
privileged).

Split the same way as `market/regime.py` / `mechanical/ratios.py`: a PURE
core (`rank_portfolios`, directly unit-testable) and a thin async DB layer
(`build_snapshot`) that reads the UC6-updated Portfolio vertices, writes
`portfolio_weekly_snapshot`, and appends a RankingEvent.
"""

import dataclasses
import json
from datetime import date
from typing import Any

from investment.db.sqlite import InvestmentDB
from investment.market.regime import FRAMEWORK_ID

GAP_FIELDS = ("sharpe_rolling", "sortino_rolling", "calmar_rolling", "max_drawdown")

# -- pure core ---------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ValuationRow:
    portfolio_id: str
    defender: bool
    framework_id: str
    designed_regime_type_id: str | None
    primary_strategy_id: str | None
    allocation: dict[str, float]
    sharpe_rolling: float | None
    sortino_rolling: float | None
    calmar_rolling: float | None
    max_drawdown: float | None
    volatility: float | None
    return_3m: float | None
    return_6m: float | None
    return_1y: float | None
    return_3y: float | None
    return_5y: float | None


@dataclasses.dataclass(frozen=True)
class RankedRow:
    row: ValuationRow
    rank: int
    gap_to_defender: dict[str, float | None] | None


def _gap_to_defender(row: ValuationRow, defender: ValuationRow) -> dict[str, float | None]:
    def delta(a: float | None, b: float | None) -> float | None:
        return None if a is None or b is None else round(a - b, 6)

    return {field: delta(getattr(row, field), getattr(defender, field)) for field in GAP_FIELDS}


def _indicator(value: float | None) -> float:
    """A missing indicator ranks last on every key (never silently 0.0, which
    would beat a legitimately negative Sortino)."""
    return value if value is not None else float("-inf")


def _order_by_rule(rows: list[ValuationRow], tiebreak_window: float) -> list[ValuationRow]:
    """The Sortino-GROUPED order of docs/DATA_MODELS.md 'Ranking rule'.

    Walking down the Sortino-sorted list, a portfolio stays in the current
    group while it is within `tiebreak_window` of that GROUP'S LEADER (the
    group's highest Sortino); otherwise it opens a new group and leads it.
    Ordering is then the plain tuple key `(group, -calmar, -max_drawdown)`.

    The grouping is what makes this a real total order, and is the reason
    ranking is not a pairwise comparator: "tied within 0.02" is NOT transitive
    (Sortinos 1.00 / 1.015 / 1.03 — A ties B, B ties C, but C beats A
    outright), so under a pairwise reading no consistent ranking exists and the
    result depends on the order rows happen to be compared in. Anchoring each
    group to its leader removes the cycle by construction, which the Phase 9
    replay needs: it calibrates thresholds on this output over thousands of
    weekly rankings (docs/MILESTONES.md M6)."""
    by_sortino = sorted(rows, key=lambda r: -_indicator(r.sortino_rolling))

    grouped: list[tuple[int, ValuationRow]] = []
    group = 0
    leader: float | None = None
    for row in by_sortino:
        sortino = _indicator(row.sortino_rolling)
        # `leader - sortino` is nan when both are -inf (all-missing Sortinos);
        # nan > window is False, so they stay one group — which is right, they
        # are equally unranked.
        if leader is None:
            leader = sortino
        elif leader - sortino > tiebreak_window:
            group += 1
            leader = sortino
        grouped.append((group, row))

    def sort_key(entry: tuple[int, ValuationRow]) -> tuple[int, float, float]:
        group_index, row = entry
        return (group_index, -_indicator(row.calmar_rolling), -_indicator(row.max_drawdown))

    # sorted() is stable, so rows tied on the whole key keep their Sortino
    # order, and below that the caller's input order — deterministic because
    # `_valuation_rows` pins it with ORDER BY portfolio.id.
    return [row for _, row in sorted(grouped, key=sort_key)]


def rank_portfolios(rows: list[ValuationRow], tiebreak_window: float) -> list[RankedRow]:
    """docs/DATA_MODELS.md 'Ranking rule': `sortino_rolling` DESC, Sortino ties
    GROUPED against the group leader (see `_order_by_rule`); within a group
    `calmar_rolling` DESC, then `max_drawdown` (less negative wins).
    `calmar_rolling < 1.0` is demoted below every eligible row regardless of
    Sortino (Invariant#calmar-accumulation gate) — the demoted rows are ranked
    among themselves by the same rule."""
    if not rows:
        return []
    defender = next((r for r in rows if r.defender), None)
    if defender is None:
        raise ValueError("rank_portfolios: no defender in rows")

    def eligible(r: ValuationRow) -> bool:
        return (r.calmar_rolling or 0.0) >= 1.0

    ordered = _order_by_rule([r for r in rows if eligible(r)], tiebreak_window) + _order_by_rule(
        [r for r in rows if not eligible(r)], tiebreak_window
    )

    return [
        RankedRow(
            row=row,
            rank=i,
            gap_to_defender=None if row.defender else _gap_to_defender(row, defender),
        )
        for i, row in enumerate(ordered, start=1)
    ]


# -- async DB layer (writer path — agent-only, ADR-004/ADR-005) ------------


def _sign(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 0:
        return "+"
    if value < 0:
        return "-"
    return "~"


def _liquidity_direction(level: float | None, speed: float | None) -> str | None:
    """Mirrors `market/regime.py` `derive_tags`' liquidity thresholds."""
    if level is None or speed is None:
        return None
    if level < 100 and speed < 0:
        return "tightening"
    if level > 100 and speed > 0:
        return "easing"
    return "neutral"


async def _latest_market_row(db: InvestmentDB, ticker: str) -> dict[str, Any] | None:
    rows = await db.query(
        "SELECT level, speed, acceleration FROM market_data WHERE ticker = :t "
        "ORDER BY ts DESC LIMIT 1",
        t=ticker,
    )
    return rows[0] if rows else None


async def _market_context(db: InvestmentDB) -> dict[str, Any]:
    """docs/USE_CASES.md UC7 example payload shape."""
    regime_rows = await db.query(
        "SELECT regime.regime_type_id, regime.confidence, regime_type.aliases, "
        "regime_type.framework_id FROM regime "
        "JOIN regime_type ON regime_type.id = regime.regime_type_id "
        "WHERE regime.is_current = 1"
    )
    if not regime_rows:
        return {
            "framework": FRAMEWORK_ID,
            "regime": None,
            "aliases": [],
            "confidence": None,
            "global_liquidity": None,
            "derivatives": {"inflation_speed": None, "growth_acceleration": None},
        }
    r = regime_rows[0]
    liquidity = await _latest_market_row(db, "GLOBAL_LIQUIDITY")
    inflation = await _latest_market_row(db, "CPIAUCSL")
    growth = await _latest_market_row(db, "GROWTH_COMPOSITE")
    return {
        "framework": r["framework_id"],
        "regime": r["regime_type_id"],
        "aliases": json.loads(r["aliases"]) if r["aliases"] else [],
        "confidence": r["confidence"],
        "global_liquidity": _liquidity_direction(
            liquidity["level"] if liquidity else None, liquidity["speed"] if liquidity else None
        ),
        "derivatives": {
            "inflation_speed": _sign(inflation["speed"] if inflation else None),
            "growth_acceleration": _sign(growth["acceleration"] if growth else None),
        },
    }


async def _valuation_rows(db: InvestmentDB) -> list[ValuationRow]:
    """ORDER BY portfolio.id is load-bearing, not cosmetic: SQLite guarantees
    no row order without it, and `rank_portfolios`' tie-break is a WINDOW
    comparison ("within `ranking_tiebreak_window`"), which is not transitive —
    so the ranking of near-tied portfolios can depend on the order they arrive
    in. A stable, content-independent input order makes the ranking
    reproducible run-to-run and, above all, replayable (Phase 9 / M6 calibrates
    thresholds on this output; a ranking that can shuffle under a VACUUM or a
    query-planner change is not evidence)."""
    rows = await db.query(
        "SELECT portfolio.id, portfolio.defender, portfolio.framework_id, portfolio.allocation, "
        "portfolio.sharpe_rolling, portfolio.sortino_rolling, portfolio.calmar_rolling, "
        "portfolio.max_drawdown, portfolio.volatility, portfolio.return_3m, portfolio.return_6m, "
        "portfolio.return_1y, portfolio.return_3y, portfolio.return_5y, "
        "(SELECT regime_type_id FROM designed_for "
        " WHERE designed_for.portfolio_id = portfolio.id LIMIT 1) AS designed_regime_type_id, "
        "(SELECT strategy_id FROM holds "
        " WHERE holds.portfolio_id = portfolio.id AND holds.is_primary = 1 LIMIT 1) "
        " AS primary_strategy_id "
        "FROM portfolio WHERE enabled = 1 ORDER BY portfolio.id"
    )
    return [
        ValuationRow(
            portfolio_id=r["id"],
            defender=bool(r["defender"]),
            framework_id=r["framework_id"],
            designed_regime_type_id=r["designed_regime_type_id"],
            primary_strategy_id=r["primary_strategy_id"],
            allocation=json.loads(r["allocation"]),
            sharpe_rolling=r["sharpe_rolling"],
            sortino_rolling=r["sortino_rolling"],
            calmar_rolling=r["calmar_rolling"],
            max_drawdown=r["max_drawdown"],
            volatility=r["volatility"],
            return_3m=r["return_3m"],
            return_6m=r["return_6m"],
            return_1y=r["return_1y"],
            return_3y=r["return_3y"],
            return_5y=r["return_5y"],
        )
        for r in rows
    ]


async def build_snapshot(
    db: InvestmentDB, tiebreak_window: float, snapshot_date: date | None = None
) -> list[RankedRow]:
    """UC7 — ranks every enabled Portfolio (UC6 must have run first — this
    reads Portfolio.sharpe_rolling/etc, it does not compute them), writes one
    `portfolio_weekly_snapshot` row per portfolio, and appends a RankingEvent
    for the batch BEFORE the snapshot rows (CLAUDE.md 'EventLog' rule)."""
    snapshot_date = snapshot_date or date.today()
    valuation_rows = await _valuation_rows(db)
    if not valuation_rows:
        return []
    ranked = rank_portfolios(valuation_rows, tiebreak_window)
    context = await _market_context(db)
    trace = (
        "Mechanical ranking: sortino_rolling DESC, tie-break within "
        f"{tiebreak_window} = calmar_rolling DESC, final tie-break = max_drawdown "
        "(docs/DATA_MODELS.md 'Ranking rule')."
    )

    async with db.transaction():
        await db.append_event(
            type="RankingEvent",
            source_uc="UC7",
            source_id=None,
            payload={
                "market_context": context,
                "ranking": [
                    {
                        "rank": rr.rank,
                        "portfolio_id": rr.row.portfolio_id,
                        "defender": rr.row.defender,
                        "sortino_rolling": rr.row.sortino_rolling,
                        "calmar_rolling": rr.row.calmar_rolling,
                    }
                    for rr in ranked
                ],
            },
            event_date=snapshot_date,
        )
        for rr in ranked:
            row = rr.row
            await db.command(
                "INSERT OR REPLACE INTO portfolio_weekly_snapshot "
                "(date, portfolio_id, defender, framework_id, designed_regime_type_id, "
                " primary_strategy_id, allocation, rank, sharpe_rolling, sortino_rolling, "
                " calmar_rolling, max_drawdown, volatility, return_3m, return_6m, return_1y, "
                " return_3y, return_5y, gap_to_defender, market_context, recommendation, trace) "
                "VALUES (:date, :portfolio_id, :defender, :framework_id, "
                " :designed_regime_type_id, :primary_strategy_id, :allocation, :rank, :sharpe, "
                " :sortino, :calmar, :mdd, :vol, :r3m, :r6m, :r1y, :r3y, :r5y, :gap, :context, "
                " :recommendation, :trace)",
                date=snapshot_date.isoformat(),
                portfolio_id=row.portfolio_id,
                defender=row.defender,
                framework_id=row.framework_id,
                designed_regime_type_id=row.designed_regime_type_id,
                primary_strategy_id=row.primary_strategy_id,
                allocation=json.dumps(row.allocation),
                rank=rr.rank,
                sharpe=row.sharpe_rolling,
                sortino=row.sortino_rolling,
                calmar=row.calmar_rolling,
                mdd=row.max_drawdown,
                vol=row.volatility,
                r3m=row.return_3m,
                r6m=row.return_6m,
                r1y=row.return_1y,
                r3y=row.return_3y,
                r5y=row.return_5y,
                gap=json.dumps(rr.gap_to_defender) if rr.gap_to_defender is not None else None,
                context=json.dumps(context),
                # 'maintain' for the defender, 'monitor' for challengers —
                # Writeback (M8) upgrades to 'paper-test' after a proposal
                # gate passes (docs/TASKS.md portfolio_weekly_snapshot spec).
                recommendation="maintain" if row.defender else "monitor",
                trace=trace,
            )

    return ranked
