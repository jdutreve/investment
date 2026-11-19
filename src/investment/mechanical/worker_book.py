"""The Worker's own book (ADR-016, owner 2026-11-19).

Beside reading the mechanical stack, the Worker manages one portfolio of its own,
`worker-portfolio`, tracked and ranked with every other: each cycle it may set an
allocation over the system's investable tickers and cash, long only, summing to
100. It never touches the stack (ADR-012 stands for the adopted strategy).

Two halves, both mechanical:
- RECORD: an allocation is journalled (`WorkerBookEvent`) and written as the
  book's current allocation. It takes effect on the next business day — decided on
  what was knowable when the Worker spoke, held from the next session (ADR-003).
- MEASURE: the NAV is rebuilt from the journal by `replay.shadow_book_nav`, the
  engine every time-varying book uses, charged `ratios.TRADING_COST_BPS` per order
  like every NAV (ADR-010), and persisted by `ratios.persist_nav`, so it is ranked
  on the same indicators as the others.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios
from investment.mechanical.backtests import investable_tickers
from investment.mechanical.market_signal import STACK_TICKERS
from investment.mechanical.replay import shadow_book_nav
from investment.worker.result import WorkerBook

logger = logging.getLogger(__name__)

PORTFOLIO_ID = "worker-portfolio"
BOOK_EVENT = "WorkerBookEvent"
ROLLING_WINDOW_DAYS = 756


@dataclass(frozen=True)
class BookRefresh:
    rows_written: int
    inception: str | None


async def book_universe(db: InvestmentDB) -> list[str]:
    """What the book may hold: every priced sleeve of a benchmark class, the
    stack's own sleeves, and cash."""
    return sorted(set(await investable_tickers(db)) | set(STACK_TICKERS))


def effective_date(decided_on: date) -> date:
    """The next business day: held from the session after the decision."""
    return (pd.Timestamp(decided_on) + pd.offsets.BDay(1)).date()


async def record_book(db: InvestmentDB, book: WorkerBook, today: date) -> dict[str, float] | None:
    """Journal the Worker's allocation and make it the book's current one.
    A ticker outside the universe refuses the whole allocation (logged): the
    book keeps the one in force rather than holding a guess."""
    universe = set(await book_universe(db))
    unknown = sorted(set(book.allocation) - universe)
    if unknown:
        logger.warning("worker book refused: %s outside the universe", unknown)
        return None
    allocation = {t: round(w, 4) for t, w in book.allocation.items() if w > 0}
    effective = effective_date(today)
    async with db.transaction() as tx:
        await tx.append_event(
            type=BOOK_EVENT,
            source_uc="uc8",
            source_id=PORTFOLIO_ID,
            payload={
                "allocation": allocation,
                "effective": effective.isoformat(),
                "rationale": book.rationale,
            },
            event_date=today,
        )
        await tx.command(
            "UPDATE portfolio SET allocation = :a, updated_at = :now WHERE id = :id",
            a=json.dumps(allocation),
            now=datetime.now(UTC).isoformat(),
            id=PORTFOLIO_ID,
        )
    return allocation


async def book_targets(db: InvestmentDB) -> dict[pd.Timestamp, dict[str, float]]:
    """Every allocation the Worker set, by the date it took effect; a later
    decision for the same day replaces an earlier one."""
    rows = await db.query("SELECT payload FROM event_log WHERE type = :t ORDER BY id", t=BOOK_EVENT)
    targets: dict[pd.Timestamp, dict[str, float]] = {}
    for row in rows:
        payload: dict[str, Any] = json.loads(str(row["payload"]))
        targets[pd.Timestamp(payload["effective"])] = {
            str(t): float(w) for t, w in payload["allocation"].items()
        }
    return targets


async def refresh_book_nav(db: InvestmentDB, window: int = ROLLING_WINDOW_DAYS) -> BookRefresh:
    """Rebuild the book's NAV from its journal and persist it. Nothing to do
    before the first allocation has a session to be held in."""
    targets = await book_targets(db)
    if not targets:
        return BookRefresh(0, None)
    tickers = sorted({t for w in targets.values() for t in w if t != ratios.CASH_TICKER})
    prices = {t: await ratios.load_price(db, t) for t in tickers}
    rf = await ratios.load_rf_daily(db)
    sessions = rf.index if not tickers else pd.concat(prices, axis=1).dropna().index
    nav, _turnover = shadow_book_nav(
        targets, prices, rf, ratios.TRADING_COST_BPS, pd.DatetimeIndex(sessions)
    )
    inception = min(targets).date().isoformat()
    if nav.empty:
        logger.info("worker book: no session yet since its first allocation (%s)", inception)
        return BookRefresh(0, inception)
    result = await ratios.persist_nav(db, PORTFOLIO_ID, nav.dropna(), window)
    return BookRefresh(result.rows_written, inception)


COMPARED_WITH = ("ms-stack", "spy-USD", "all-weather-USD")


async def book_context(db: InvestmentDB) -> dict[str, Any]:
    """What the Worker reads about its own book: the allocation in force, what
    it may hold, and how the book has done since its first allocation against
    the stack and the two benchmarks over the same days."""
    rows = await db.query("SELECT allocation FROM portfolio WHERE id = :id", id=PORTFOLIO_ID)
    allocation = json.loads(str(rows[0]["allocation"])) if rows else {}
    targets = await book_targets(db)
    inception = min(targets).date().isoformat() if targets else None
    returns: dict[str, float] = {}
    if inception:
        book = await ratios.load_nav(db, PORTFOLIO_ID)
        if not book.empty:
            last = book.index.max()
            for pid in (PORTFOLIO_ID, *COMPARED_WITH):
                nav = (await ratios.load_nav(db, pid)).loc[pd.Timestamp(inception) : last]
                if len(nav) >= 2:
                    returns[pid] = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    return {
        "allocation": allocation,
        "inception": inception,
        "universe": await book_universe(db),
        "since_inception": returns,
    }


def describe_book(book: dict[str, Any]) -> list[str]:
    """The book as prompt lines."""
    lines = [
        "YOUR OWN BOOK — the portfolio you manage yourself, tracked and ranked with the "
        "others. Set `my_book` to change it (held from the next session, "
        f"{ratios.TRADING_COST_BPS:g} bps per order, measured like every portfolio), "
        "or null to keep it.",
        f"  In force: {book['allocation']}",
        f"  May hold: {', '.join(book['universe'])}",
    ]
    if book["inception"] is None:
        lines.append("  No allocation yet: the book is all cash until you set one.")
    elif not book["since_inception"]:
        lines.append(
            f"  First allocation took effect {book['inception']}; no session measured yet."
        )
    else:
        parts = ", ".join(f"{pid} {r:+.1%}" for pid, r in book["since_inception"].items())
        lines.append(f"  Since {book['inception']}: {parts}")
    return lines
