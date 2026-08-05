"""The mechanical Monday chain run against an as-of-t snapshot (M8b —
docs/TASKS.md Task 9.4; src/investment/mechanical/as_of_cycle.py).

The four jobs each have their own tests. What is new here is the composition:
the chain's ORDER (UC7 ranking on indicators UC6 has refilled, not on the NULLs
the snapshot left), and that every artefact it writes is dated at the DECISION
date rather than at the wall clock — a 2026 stamp on a 2008 reading is a leak of
today into the past, and `ts` is what every later reader sorts on.

Run against a REAL pruned snapshot, not a hand-built database: the pruning and
the hydration are two halves of one guarantee and testing them apart would miss
exactly the seam where the ranking reads a blanked column.
"""

import json
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from investment.db.as_of_snapshot import build_as_of_snapshot, snapshot_path
from investment.db.sqlite import InvestmentDB
from investment.mechanical.as_of_cycle import run_as_of_cycle
from investment.mechanical.market_signal import BOOK_PORTFOLIO_IDS, STACK_TICKERS

AS_OF = date(2008, 10, 1)
# The 200d overlay needs 200 prints and the signal medians want 252, so the
# window opens well before t; it closes after, to give the prune something to cut.
START = date(2006, 1, 1)
END = date(2009, 12, 31)
THRESHOLDS = {
    "rolling_window_days": 756.0,
    "ranking_tiebreak_window": 0.02,
    "min_backtest_periods": 3.0,
}


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    for key, value in THRESHOLDS.items():
        await cmd(
            "INSERT INTO system_thresholds (key, value, description, updated_at) "
            "VALUES (:k, :v, 'd', '2026-01-01')",
            k=key,
            v=value,
        )
    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('fw', 'F', 1, 'tr', '1990-01-01')"
    )
    await cmd(
        "INSERT INTO user_profile (user_id, currency, benchmark, phase, horizon_years, "
        "max_drawdown_pct, max_single_asset_pct, created_at, updated_at) VALUES ('u', 'USD', "
        "'b', 'accumulation', 12, -25.0, 50.0, '1990-01-01', '1990-01-01')"
    )
    # Indicators seeded at 2026 values — the snapshot must blank them and the
    # cycle must refill them from the NAV series below.
    await cmd(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, sortino_rolling, "
        "calmar_rolling, max_drawdown, trace, updated_at) VALUES ('pf', 'P', 'fw', 1, 1, 'USD', "
        "'b', '{\"SPY\": 100}', -15.0, 50.0, 'accumulation', 9.99, 9.99, -0.01, 'tr', "
        "'2026-07-15')"
    )
    # The market-signal stack ADR-011 makes the judge: its three books, the five
    # sleeves they hold, and the two market-priced signals that pick between
    # them. Not scaffolding — `run_as_of_cycle` now runs the live monthly
    # decision, and that path refuses (rightly) to run on a missing sleeve.
    for book_id in BOOK_PORTFOLIO_IDS.values():
        await cmd(
            "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, "
            "benchmark, allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, "
            "updated_at) VALUES (:id, :id, 'fw', 0, 0, 'USD', 'b', '{}', -25.0, 50.0, "
            "'accumulation', 'tr', '2026-01-01')",
            id=book_id,
        )
    for ticker in STACK_TICKERS:
        await cmd(
            "INSERT INTO allowed_tickers (ticker, asset_class, currency, source, transform, "
            "active) VALUES (:t, 'equities', 'USD', 'yahoo', 'none', 1)",
            t=ticker,
        )

    # Daily rows across the whole window. The rolling NAV columns are persisted
    # (UC6 reads them, it does not recompute them) and DIFFER either side of t,
    # so a cycle that read the post-t rows would show up in the assertions.
    days = (END - START).days + 1
    await db.append_ts_batch(
        "portfolio_nav",
        [
            {
                "portfolio_id": "pf",
                "currency": "USD",
                "ts": (START + timedelta(days=i)).isoformat(),
                "nav": 100.0 + i * 0.1,
                "daily_return": 0.001,
                "sharpe_rolling": 0.5,
                "drawdown": -0.08,
                "sortino_rolling": 1.25 if START + timedelta(days=i) <= AS_OF else 7.77,
                "calmar_rolling": 1.50 if START + timedelta(days=i) <= AS_OF else 7.77,
            }
            for i in range(days)
        ],
    )
    rising = [100.0 + i * 0.1 for i in range(days)]
    series: dict[str, list[float]] = {t: rising for t in STACK_TICKERS}
    series["^IRX"] = [2.0] * days
    series["BAA10Y"] = [1.0] * days  # flat -> equals its own median -> TIGHT
    series["T10Y2Y"] = [2.0] * days  # flat and positive -> STEEP
    for ticker, values in series.items():
        await db.append_ts_batch(
            "market_data",
            [
                {
                    "ts": (START + timedelta(days=i)).isoformat(),
                    "ticker": ticker,
                    "asset_class": "equities",
                    "currency": "USD",
                    "level": value,
                }
                for i, value in enumerate(values)
            ],
        )


@pytest.fixture
async def snapshot(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    live = tmp_path / "live.db"
    db = InvestmentDB(live)
    await _seed(db)
    await db.close()

    build_as_of_snapshot(live, snapshot_path(tmp_path, AS_OF), AS_OF)
    snap = InvestmentDB(snapshot_path(tmp_path, AS_OF))
    yield snap
    await snap.close()


async def test_the_cycle_refills_what_the_prune_blanked(snapshot: InvestmentDB) -> None:
    """UC6 runs before UC7 for this reason: the snapshot blanks the Portfolio
    indicators, so a ranking built first would sort on NULLs."""
    before = await snapshot.query("SELECT sortino_rolling FROM portfolio WHERE id = 'pf'")
    assert before[0]["sortino_rolling"] is None

    cycle = await run_as_of_cycle(snapshot, AS_OF)

    after = await snapshot.query("SELECT sortino_rolling, calmar_rolling FROM portfolio")
    # The as-of values, not the 9.99 the live row carried nor the 7.77 of the
    # NAV rows that were pruned away.
    assert after[0]["sortino_rolling"] == 1.25
    assert after[0]["calmar_rolling"] == 1.50
    assert cycle.portfolios_valued == 1
    assert cycle.portfolios_ranked == 1


async def test_every_artefact_is_dated_at_the_decision_date(snapshot: InvestmentDB) -> None:
    """Not at the wall clock. `build_snapshot` already took a date; the FAVORS
    and scenario-probability jobs did not, and stamped `date.today()`."""
    await run_as_of_cycle(snapshot, AS_OF)

    ranking = await snapshot.query("SELECT DISTINCT date FROM portfolio_weekly_snapshot")
    assert [r["date"] for r in ranking] == [AS_OF.isoformat()]

    ranked = await snapshot.query(
        "SELECT rank, sortino_rolling FROM portfolio_weekly_snapshot WHERE date = :d",
        d=AS_OF.isoformat(),
    )
    assert ranked[0]["rank"] == 1
    assert ranked[0]["sortino_rolling"] == 1.25  # the as-of indicator, ranked


async def test_the_ranking_event_carries_the_decision_date(snapshot: InvestmentDB) -> None:
    """The EventLog is the audit spine: a replayed cycle whose events are dated
    today cannot be told apart from a live one afterwards."""
    await run_as_of_cycle(snapshot, AS_OF)

    events = await snapshot.query(
        "SELECT type, event_date FROM event_log WHERE type = 'RankingEvent'"
    )
    assert [r["event_date"] for r in events] == [AS_OF.isoformat()]


async def test_the_market_signal_decides_at_the_replayed_date(snapshot: InvestmentDB) -> None:
    """ADR-011's judge, journalled BEFORE the Worker ever speaks. Without this
    step the replayed Worker reads `market_signal: {}` and the screen validates
    only the retained bridge — the path ADR-007 superseded."""
    cycle = await run_as_of_cycle(snapshot, AS_OF)

    assert cycle.market_signal_decision == AS_OF.isoformat()
    events = await snapshot.query(
        "SELECT event_date, payload FROM event_log WHERE type = 'MarketSignalDecisionEvent'"
    )
    assert [r["event_date"] for r in events] == [AS_OF.isoformat()]
    # Flat spread reads TIGHT, positive slope reads STEEP -> the slowdown book.
    payload = json.loads(str(events[0]["payload"]))
    assert payload["signal_state"] == "credit-spread-tight-yield-curve-steep"
    assert payload["decision_date"] == AS_OF.isoformat()
    # Every input carries the date it became knowable, and none is after t.
    knowable = [sig["knowable_at"] for sig in payload["signals"].values()]
    assert knowable and all(k <= AS_OF.isoformat() for k in knowable)


async def test_the_planner_baseline_sees_that_decision(snapshot: InvestmentDB) -> None:
    """The end the whole hydration serves: what the Worker actually reads at a
    replayed date is a populated baseline, not an empty one."""
    from investment.planner.baseline import gather_baseline

    await run_as_of_cycle(snapshot, AS_OF)
    baseline = await gather_baseline(snapshot)

    assert baseline.ranking and baseline.ranking[0]["rank"] == 1
    assert baseline.market_signal  # ADR-007's decision, not {}
    assert baseline.scenarios == [] or all(s["ts"] == AS_OF.isoformat() for s in baseline.scenarios)
