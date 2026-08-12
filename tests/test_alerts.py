"""Live-path health alerts (mechanical/alerts.py, docs/DECISIONS.md ADR-009).

WHY THIS FILE EXISTS. ADR-009 removed the drawdown GATE and replaced it with an
alert, which makes this module the whole of what the system does when the -25%
rule is breached, when the data feeding the trend overlay dies, or when the
stack stops reproducing the pair ADR-007 was signed on. It shipped
untested. Every assertion below is about a failure that is SILENT by
construction — a dead feed and a stuck cycle both look exactly like a normal
holding month — so an alert that does not fire is indistinguishable from
health, and only a test can tell the two apart.

Real throwaway SQLite, no mocks (CLAUDE.md "Tests").
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from investment import market_signal_cycle as MSC
from investment.db.sqlite import InvestmentDB
from investment.mechanical import alerts as A
from investment.mechanical import market_signal as MS
from investment.mechanical.market_signal import STACK_PORTFOLIO_ID, STACK_TICKERS

TODAY = date(2026, 8, 3)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "alerts.db")
    await conn.command(
        "INSERT INTO user_profile (user_id, currency, benchmark, max_drawdown_pct, "
        "max_single_asset_pct, phase, created_at, updated_at) VALUES ('u', 'CHF', 'b', -25.0, "
        "50.0, 'accumulation', '2026-01-01', '2026-01-01')"
    )
    yield conn
    await conn.close()


async def _stack_nav(db: InvestmentDB, drawdown: float, ts: date = TODAY) -> None:
    """One `portfolio_nav` row for the stack — the alert reads the LATEST
    non-NULL `drawdown`, nothing else."""
    await db.append_ts_batch(
        "portfolio_nav",
        [
            {
                "portfolio_id": STACK_PORTFOLIO_ID,
                "currency": "USD",
                "ts": ts.isoformat(),
                "nav": 100.0,
                "drawdown": drawdown,
            }
        ],
    )


async def _prices(db: InvestmentDB, latest: date, tickers: tuple[str, ...] = STACK_TICKERS) -> None:
    await db.append_ts_batch(
        "market_data",
        [
            {
                "ts": latest.isoformat(),
                "ticker": ticker,
                "asset_class": "equities",
                "currency": "USD",
                "level": 100.0,
            }
            for ticker in tickers
        ],
    )


async def _decision_event(db: InvestmentDB, event_date: date) -> None:
    async with db.transaction():
        await db.append_event(
            type="MarketSignalDecisionEvent",
            source_uc="UC8",
            source_id=None,
            payload={"decision_date": event_date.isoformat()},
            event_date=event_date,
        )


# -- drawdown ---------------------------------------------------------------


async def test_drawdown_inside_the_rule_says_nothing(db: InvestmentDB) -> None:
    await _stack_nav(db, -0.238)  # the pinned -23.8%, inside the -25% rule
    assert await A.stack_drawdown_alert(db) is None


async def test_drawdown_past_the_rule_is_critical_and_names_the_measure(
    db: InvestmentDB,
) -> None:
    await _stack_nav(db, -0.312)
    alert = await A.stack_drawdown_alert(db)
    assert alert is not None
    assert alert.level == "critical" and alert.code == "stack_drawdown"
    # The message must describe what `rolling_max_drawdown` actually is — the
    # DEEPEST drawdown inside the trailing 756 days, not today's distance from a
    # high — and must not claim to speak about the owner's account.
    assert "-31.2%" in alert.message
    assert "deepest drawdown over the last 36 months" in alert.message
    assert "PAPER" in alert.message
    # ADR-009's whole point: it tells, it never blocks.
    assert "Nothing has been blocked" in alert.message


async def test_drawdown_alert_is_silent_before_the_stack_has_a_nav(db: InvestmentDB) -> None:
    """Nothing to say is not the same as "inside the limit" — and printing 0%
    would read as health."""
    assert await A.stack_drawdown_alert(db) is None


async def test_drawdown_alert_needs_a_user_profile(db: InvestmentDB) -> None:
    await db.command("DELETE FROM user_profile")
    await _stack_nav(db, -0.90)
    assert await A.stack_drawdown_alert(db) is None


# -- market-data freshness --------------------------------------------------


async def test_fresh_prices_say_nothing(db: InvestmentDB) -> None:
    await _prices(db, TODAY - timedelta(days=3))
    assert await A.market_data_freshness_alert(db, TODAY) is None


async def test_stale_prices_are_critical(db: InvestmentDB) -> None:
    await _prices(db, TODAY - timedelta(days=20))
    alert = await A.market_data_freshness_alert(db, TODAY)
    assert alert is not None
    assert alert.code == "market_data_stale" and alert.level == "critical"
    assert "20 days ago" in alert.message


async def test_the_oldest_sleeve_binds_not_the_newest(db: InvestmentDB) -> None:
    """THE failure this check exists for: a table-wide `MAX(ts)` reads fresh off
    any one series still updating while the feed the 200d overlay depends on is
    dead. Four fresh sleeves must not hide one stale one."""
    await _prices(db, TODAY, tickers=("SPY", "IWN", "GLD", "VCIT"))
    await _prices(db, TODAY - timedelta(days=30), tickers=("IEF",))
    alert = await A.market_data_freshness_alert(db, TODAY)
    assert alert is not None and alert.code == "market_data_stale"


async def test_macro_series_lagging_by_design_do_not_trip_the_alert(db: InvestmentDB) -> None:
    """CPI and friends publish monthly; the check is scoped to the stack's own
    price sleeves precisely so their lag is not read as a dead feed."""
    await _prices(db, TODAY)
    await _prices(db, TODAY - timedelta(days=60), tickers=("CPIAUCSL",))
    assert await A.market_data_freshness_alert(db, TODAY) is None


async def test_a_sleeve_with_no_data_at_all_is_reported(db: InvestmentDB) -> None:
    """A sleeve with ZERO rows produces no GROUP BY row, so it is invisible to a
    `min` over what came back — the worst case reading as the healthiest."""
    await _prices(db, TODAY, tickers=("SPY", "IWN", "GLD", "VCIT"))
    alert = await A.market_data_freshness_alert(db, TODAY)
    assert alert is not None
    assert alert.code == "market_data_missing" and alert.level == "critical"
    assert "IEF" in alert.message


async def test_an_empty_market_data_table_is_reported_not_ignored(db: InvestmentDB) -> None:
    alert = await A.market_data_freshness_alert(db, TODAY)
    assert alert is not None and alert.code == "market_data_missing"


# -- signal freshness -------------------------------------------------------


async def test_fresh_signals_say_nothing(db: InvestmentDB) -> None:
    await _prices(db, TODAY - timedelta(days=3), tickers=A.SIGNAL_TICKERS)
    assert await A.signal_freshness_alert(db, TODAY) is None


async def test_stale_signals_are_critical_and_say_the_decision_still_runs(
    db: InvestmentDB,
) -> None:
    """The failure that separates this check from the sleeves': the forward fill
    carries the last print, so the decision does NOT stop — it picks a book from
    a spread quoted weeks ago, and the message has to say so or the owner reads
    a normal-looking month."""
    await _prices(db, TODAY - timedelta(days=20), tickers=A.SIGNAL_TICKERS)
    alert = await A.signal_freshness_alert(db, TODAY)
    assert alert is not None
    assert alert.code == "signal_data_stale" and alert.level == "critical"
    assert "20 days ago" in alert.message
    assert "still runs" in alert.message


async def test_the_older_of_the_two_signals_binds(db: InvestmentDB) -> None:
    await _prices(db, TODAY, tickers=(A.CREDIT_SPREAD,))
    await _prices(db, TODAY - timedelta(days=30), tickers=(A.YIELD_SLOPE,))
    alert = await A.signal_freshness_alert(db, TODAY)
    assert alert is not None and alert.code == "signal_data_stale"


async def test_an_absent_signal_is_reported_and_names_itself(db: InvestmentDB) -> None:
    """`run_market_signal` RAISES on this rather than defaulting to the
    90%-equity book; the alert is the Monday-morning explanation of that abort."""
    await _prices(db, TODAY, tickers=(A.CREDIT_SPREAD,))
    alert = await A.signal_freshness_alert(db, TODAY)
    assert alert is not None
    assert alert.code == "signal_data_missing" and alert.level == "critical"
    assert A.YIELD_SLOPE in alert.message


async def test_fresh_sleeves_do_not_vouch_for_the_signals(db: InvestmentDB) -> None:
    """The two groups are watched separately because they fail separately: a
    perfectly healthy price feed says nothing about whether the book being
    chosen is still informed."""
    await _prices(db, TODAY)
    await _prices(db, TODAY - timedelta(days=40), tickers=A.SIGNAL_TICKERS)
    assert await A.market_data_freshness_alert(db, TODAY) is None
    assert (await A.signal_freshness_alert(db, TODAY)) is not None


# -- decision freshness -----------------------------------------------------


async def test_a_decision_within_the_month_says_nothing(db: InvestmentDB) -> None:
    await _decision_event(db, TODAY - timedelta(days=20))
    assert await A.decision_freshness_alert(db, TODAY) is None


async def test_a_missed_monthly_anchor_warns(db: InvestmentDB) -> None:
    await _decision_event(db, TODAY - timedelta(days=70))
    alert = await A.decision_freshness_alert(db, TODAY)
    assert alert is not None
    assert alert.code == "decision_stale" and alert.level == "warn"
    assert "70 days" in alert.message


async def test_the_threshold_is_shorter_than_two_cadences(db: InvestmentDB) -> None:
    """An alarm that takes longer to fire than the cycle it watches can never
    warn in time: one missed anchor must trip it, a normal month must not."""
    assert 31 < A.DECISION_STALE_DAYS < 62


async def test_no_decision_yet_is_not_an_alert(db: InvestmentDB) -> None:
    assert await A.decision_freshness_alert(db, TODAY) is None


async def _drift_event(
    db: InvestmentDB,
    *,
    measurable: bool = True,
    violations: list[str] | None = None,
    reason: str | None = None,
) -> None:
    """One journalled anti-drift verdict (`market_signal_cycle.DRIFT_EVENT`).

    Written by hand rather than by running `check_drift`: the alert's job is to
    READ the verdict, and a fixture that had to price 35 years to test a message
    would be testing the wrong module — and could not express the unmeasurable
    case at all."""
    check = MS.DriftCheck(
        measurable=measurable,
        cagr=MS.PINNED_CAGR if measurable else None,
        max_drawdown=MS.PINNED_MAX_DRAWDOWN if measurable else None,
        violations=violations or [],
        reason=reason,
    )
    async with db.transaction():
        await db.append_event(
            type=MSC.DRIFT_EVENT,
            source_uc="UC8",
            source_id=MSC.STRATEGY_ID,
            payload=check.as_payload(),
            event_date=TODAY,
        )


# -- anti-drift -------------------------------------------------------------


async def test_no_drift_verdict_yet_says_nothing(db: InvestmentDB) -> None:
    """A database whose cycle has never run has nothing to report — distinct
    from one that ran and passed, and both are silent."""
    assert await A.stack_drift_alert(db) is None


async def test_a_passing_verdict_says_nothing(db: InvestmentDB) -> None:
    await _drift_event(db)
    assert await A.stack_drift_alert(db) is None


async def test_drift_is_critical_and_carries_both_arms(db: InvestmentDB) -> None:
    """The message must let the owner act without opening a REPL: what diverged,
    by how much, and the two readings it could be (a rule changed unsigned, or
    the ground moved under a fixed marker)."""
    await _drift_event(db, violations=["cagr 11.20% vs pinned 11.62% (-0.42pp, tolerance 0.10pp)"])
    alert = await A.stack_drift_alert(db)
    assert alert is not None
    assert alert.level == "critical" and alert.code == "stack_drift"
    assert "11.20%" in alert.message and "11.62%" in alert.message
    assert "re-signed" in alert.message  # the first of the two readings
    assert "Nothing has been blocked" in alert.message  # ADR-009: tell, never refuse


async def test_an_unmeasurable_verdict_is_not_drift(db: InvestmentDB) -> None:
    """An as-of snapshot bounded at 2008 prices seventeen years and cannot
    answer a 35-year question. Shouting drift at a window that is merely short
    would teach the owner to skip this line — which is the only way an alert
    can fail completely."""
    await _drift_event(
        db,
        measurable=False,
        violations=["cagr 8.09% vs pinned 11.62% (-3.53pp, tolerance 0.10pp)"],
        reason="priced data stops at 2008-12-31",
    )
    assert await A.stack_drift_alert(db) is None


async def test_the_latest_verdict_wins(db: InvestmentDB) -> None:
    """Read by descending ULID, like every other journal read: the id IS the
    append order (CLAUDE.md "EventLog"). A drift that has since been resolved —
    by re-signing the pair or by a re-seed — must stop firing on the next run."""
    await _drift_event(db, violations=["cagr 11.20% vs pinned 11.62% (-0.42pp, tolerance 0.10pp)"])
    await _drift_event(db)
    assert await A.stack_drift_alert(db) is None


# -- collection -------------------------------------------------------------


async def test_collect_orders_critical_before_warn(db: InvestmentDB) -> None:
    await _stack_nav(db, -0.40)
    await _prices(db, TODAY - timedelta(days=20))
    await _prices(db, TODAY - timedelta(days=20), tickers=A.SIGNAL_TICKERS)
    await _decision_event(db, TODAY - timedelta(days=70))
    await _drift_event(db, violations=["cagr 11.20% vs pinned 11.62% (-0.42pp, tolerance 0.10pp)"])
    found = await A.collect_alerts(db, TODAY)
    assert [a.code for a in found] == [
        "stack_drawdown",
        "stack_drift",
        "market_data_stale",
        "signal_data_stale",
        "decision_stale",
    ]
    assert [a.level for a in found] == ["critical"] * 4 + ["warn"]


async def test_a_healthy_db_collects_nothing(db: InvestmentDB) -> None:
    await _stack_nav(db, -0.10)
    await _prices(db, TODAY - timedelta(days=1))
    await _prices(db, TODAY - timedelta(days=1), tickers=A.SIGNAL_TICKERS)
    await _decision_event(db, TODAY - timedelta(days=5))
    await _drift_event(db)
    assert await A.collect_alerts(db, TODAY) == []
