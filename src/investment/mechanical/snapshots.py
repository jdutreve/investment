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
from functools import cmp_to_key
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


def rank_portfolios(rows: list[ValuationRow], tiebreak_window: float) -> list[RankedRow]:
    """docs/DATA_MODELS.md 'Ranking rule': `sortino_rolling` DESC; tie-break
    WITHIN `tiebreak_window` = `calmar_rolling` DESC; final tie-break =
    `max_drawdown` (less negative wins). `calmar_rolling < 1.0` is demoted to
    the bottom regardless of Sortino.

    The "within window" tie-break is a fuzzy (relative) comparison, not a
    stable sort key, so this uses a comparator (`cmp_to_key`) rather than a
    tuple key — the spec is silent on whether that can produce a non-transitive
    order across many close values (a known property of any "tie within X"
    rule); accepted as the literal reading of the pinned rule (CLAUDE.md
    'state assumptions explicitly')."""
    if not rows:
        return []
    defender = next((r for r in rows if r.defender), None)
    if defender is None:
        raise ValueError("rank_portfolios: no defender in rows")

    def eligible(r: ValuationRow) -> bool:
        return (r.calmar_rolling or 0.0) >= 1.0

    def compare(a: ValuationRow, b: ValuationRow) -> int:
        sortino_a = a.sortino_rolling if a.sortino_rolling is not None else float("-inf")
        sortino_b = b.sortino_rolling if b.sortino_rolling is not None else float("-inf")
        if abs(sortino_a - sortino_b) > tiebreak_window:
            return -1 if sortino_a > sortino_b else 1
        calmar_a = a.calmar_rolling if a.calmar_rolling is not None else float("-inf")
        calmar_b = b.calmar_rolling if b.calmar_rolling is not None else float("-inf")
        if calmar_a != calmar_b:
            return -1 if calmar_a > calmar_b else 1
        mdd_a = a.max_drawdown if a.max_drawdown is not None else float("-inf")
        mdd_b = b.max_drawdown if b.max_drawdown is not None else float("-inf")
        if mdd_a != mdd_b:
            return -1 if mdd_a > mdd_b else 1
        return 0

    ordered = sorted((r for r in rows if eligible(r)), key=cmp_to_key(compare)) + sorted(
        (r for r in rows if not eligible(r)), key=cmp_to_key(compare)
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
        "FROM portfolio WHERE enabled = 1"
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
