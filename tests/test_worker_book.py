"""The Worker's own book (ADR-016): recorded when given, held from the next session,
measured like every time-varying book."""

import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from investment.db.sqlite import InvestmentDB
from investment.mechanical import worker_book as B
from investment.worker.result import WorkerBook

THURSDAY = date(2026, 11, 19)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "book.db")
    await conn.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('worker', 'w', 0, 't', '2026-01-01')"
    )
    await conn.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, fx_usd_exposure, trace, "
        "updated_at) VALUES ('worker-portfolio', 'w', 'worker', 0, 1, 'USD', "
        "'all-weather-USD', '{\"cash\": 100}', -25.0, 60.0, 'accumulation', 100.0, 't', "
        "'2026-01-01')"
    )
    days = pd.bdate_range("2026-11-02", "2026-12-04")
    rows = []
    for i, d in enumerate(days):
        ts = d.strftime("%Y-%m-%d")
        rows += [
            ("SPY", "US_EQUITY", ts, 100.0 * 1.01**i),
            ("GLD", "GOLD", ts, 50.0),
            ("^IRX", "RATE", ts, 0.0),
        ]
    for ticker, asset_class, ts, level in rows:
        await conn.command(
            "INSERT INTO market_data (ticker, asset_class, currency, ts, level) "
            "VALUES (:t, :a, 'USD', :ts, :l)",
            t=ticker,
            a=asset_class,
            ts=ts,
            l=level,
        )
    yield conn
    await conn.close()


def test_a_book_is_long_only_and_sums_to_100() -> None:
    WorkerBook(allocation={"SPY": 60, "cash": 40}, rationale="r")
    with pytest.raises(ValidationError, match="long only"):
        WorkerBook(allocation={"SPY": 120, "cash": -20}, rationale="r")
    with pytest.raises(ValidationError, match="sum"):
        WorkerBook(allocation={"SPY": 60}, rationale="r")


def test_a_decision_is_held_from_the_next_business_day() -> None:
    assert B.effective_date(THURSDAY) == date(2026, 11, 20)
    assert B.effective_date(date(2026, 11, 22)) == date(2026, 11, 23)  # Sunday -> Monday


async def test_a_book_is_journalled_and_becomes_the_allocation_in_force(db: InvestmentDB) -> None:
    held = await B.record_book(
        db, WorkerBook(allocation={"SPY": 70, "cash": 30}, rationale="r"), THURSDAY
    )
    assert held == {"SPY": 70, "cash": 30}
    row = (await db.query("SELECT allocation FROM portfolio WHERE id = 'worker-portfolio'"))[0]
    assert json.loads(row["allocation"]) == {"SPY": 70, "cash": 30}
    targets = await B.book_targets(db)
    assert list(targets) == [pd.Timestamp("2026-11-20")]


async def test_a_ticker_outside_the_universe_refuses_the_whole_book(db: InvestmentDB) -> None:
    book = WorkerBook(allocation={"SPY": 50, "BTC": 50}, rationale="r")
    assert await B.record_book(db, book, THURSDAY) is None
    assert await B.book_targets(db) == {}


async def test_the_nav_is_rebuilt_from_the_journal_net_of_costs(db: InvestmentDB) -> None:
    await B.record_book(db, WorkerBook(allocation={"SPY": 100}, rationale="r"), THURSDAY)
    refresh = await B.refresh_book_nav(db, window=5)
    assert refresh.inception == "2026-11-20" and refresh.rows_written > 0
    nav = await db.query(
        "SELECT ts, nav FROM portfolio_nav WHERE portfolio_id = 'worker-portfolio' ORDER BY ts"
    )
    assert nav[0]["ts"] == "2026-11-20" and nav[0]["nav"] == pytest.approx(100.0)
    # SPY rises 1% a session; the book holds it all, so its NAV rises with it.
    assert nav[1]["nav"] == pytest.approx(101.0, rel=1e-3)
    context = await B.book_context(db)
    assert context["inception"] == "2026-11-20"
    assert "worker-portfolio" in context["since_inception"]
    assert "YOUR OWN BOOK" in "\n".join(B.describe_book(context))


async def test_the_worker_reads_what_its_trading_cost_it(db: InvestmentDB) -> None:
    # A full switch from SPY to GLD pays 2 x 23 bps; it is made before the
    # session's return (the replay's pinned sequencing), so on the seed NAV of
    # 100. The gap between the gross and the net return is what the Worker
    # reads as fees.
    await B.record_book(db, WorkerBook(allocation={"SPY": 100}, rationale="r"), THURSDAY)
    await B.record_book(db, WorkerBook(allocation={"GLD": 100}, rationale="r"), date(2026, 11, 20))
    await B.refresh_book_nav(db, window=5)
    trading = (await B.book_context(db))["trading"]
    assert trading["changes"] == 1
    assert trading["turnover"] == pytest.approx(1.0)
    assert trading["fees"] == pytest.approx(0.0046)
    lines = "\n".join(B.describe_book(await B.book_context(db)))
    assert "46 bps" in lines  # the full-switch example, rendered from the constant
    assert "fees took 46.0 bps of return" in lines
