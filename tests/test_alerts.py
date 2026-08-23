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
from investment.writeback import writeback as W

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


# -- macro freshness --------------------------------------------------------


async def _macro_series(db: InvestmentDB, ticker: str, latest: date, spacing: int, n: int) -> None:
    """`n` MACRO prints ending at `latest`, one every `spacing` days — the
    cadence the alert MEASURES rather than being told."""
    await db.append_ts_batch(
        "market_data",
        [
            {
                "ts": (latest - timedelta(days=spacing * i)).isoformat(),
                "ticker": ticker,
                "asset_class": "MACRO",
                "currency": "USD",
                "level": 1.0,
            }
            for i in range(n)
        ],
    )


def test_cadence_is_the_median_gap_not_the_newest_one() -> None:
    """`_cadence_of` is pure so the arithmetic can be pinned without a DB, and
    the MEDIAN is the point: one short gap (a correction re-published two days
    later) must not convince the check that a monthly series prints every two
    days, which would then call it overdue at 16 days instead of 46."""
    stamps = [
        date(2026, 8, 20),  # newest first, as the query returns them
        date(2026, 8, 18),  # the anomalous 2-day gap
        date(2026, 7, 18),
        date(2026, 6, 18),
        date(2026, 5, 18),
    ]
    # gaps are [2, 31, 30, 31]; the median of four is (30+31)/2, and the point
    # is that the 2 does not drag it anywhere near itself.
    spacing, age = A._cadence_of(stamps, date(2026, 8, 23))
    assert spacing == 30.5
    assert age == 3
    assert age <= max(A.MACRO_OVERDUE_PERIODS * spacing, spacing + A.MACRO_OVERDUE_GRACE_DAYS)


async def test_a_monthly_series_lagging_normally_says_nothing(db: InvestmentDB) -> None:
    """The whole reason this check measures cadence instead of reusing
    MARKET_DATA_STALE_DAYS: monthly macro is ALWAYS weeks old (ADR-003), and a
    7-day rule would fire on every healthy monthly series every week."""
    await _macro_series(db, "CPIAUCSL", TODAY - timedelta(days=25), spacing=31, n=13)
    assert await A.macro_freshness_alert(db, TODAY) is None


async def test_a_monthly_series_that_missed_a_print_is_reported(db: InvestmentDB) -> None:
    """Measured 2026-08-23: M2SL, m2_yoy and m2_accel_12m had been frozen at
    2026-06-18 for 66 days — two missed monthly releases — while the Worker
    argued risk-on from "m2 accel +1.39". Nothing watched them."""
    await _macro_series(db, "M2SL", TODAY - timedelta(days=66), spacing=31, n=13)
    alert = await A.macro_freshness_alert(db, TODAY)
    assert alert is not None
    assert alert.code == "macro_data_overdue" and alert.level == "warn"
    assert "M2SL" in alert.message and "66d old" in alert.message
    # It must say the book is NOT at stake, or it reads like the signal alert.
    assert "book is unaffected" in alert.message


async def test_a_daily_series_is_judged_on_a_daily_cadence(db: InvestmentDB) -> None:
    """Same rule, different series: 20 days of silence is unremarkable for a
    monthly print and a dead feed for a daily one."""
    await _macro_series(db, "DGS10", TODAY - timedelta(days=20), spacing=1, n=13)
    alert = await A.macro_freshness_alert(db, TODAY)
    assert alert is not None and "DGS10" in alert.message


async def test_the_two_book_signals_are_not_reported_twice(db: InvestmentDB) -> None:
    """`BAA10Y`/`T10Y2Y` are MACRO rows too, so without an explicit exclusion a
    frozen signal feed would fill the digest with the same fact twice — once
    critical, once warn."""
    await _macro_series(db, A.CREDIT_SPREAD, TODAY - timedelta(days=40), spacing=1, n=13)
    await _macro_series(db, A.YIELD_SLOPE, TODAY - timedelta(days=40), spacing=1, n=13)
    assert (await A.signal_freshness_alert(db, TODAY)) is not None  # the check that owns them
    assert await A.macro_freshness_alert(db, TODAY) is None


async def test_a_series_with_too_little_history_is_not_judged(db: InvestmentDB) -> None:
    """Two prints cannot establish a cadence; guessing one would invent the
    threshold it is then measured against."""
    await _macro_series(db, "NEWSERIES", TODAY - timedelta(days=400), spacing=31, n=2)
    assert await A.macro_freshness_alert(db, TODAY) is None


async def test_the_stale_ones_are_named_oldest_first(db: InvestmentDB) -> None:
    await _macro_series(db, "M2SL", TODAY - timedelta(days=66), spacing=31, n=13)
    await _macro_series(db, "JPNASSETS", TODAY - timedelta(days=95), spacing=31, n=13)
    await _macro_series(db, "CPIAUCSL", TODAY - timedelta(days=25), spacing=31, n=13)
    alert = await A.macro_freshness_alert(db, TODAY)
    assert alert is not None
    assert alert.message.index("JPNASSETS") < alert.message.index("M2SL")
    assert "CPIAUCSL" not in alert.message  # healthy, so absent from the line


async def test_a_frozen_macro_series_does_not_touch_the_book_alerts(db: InvestmentDB) -> None:
    """The separation this check exists for: the two series that PICK the book
    are fine, so nothing critical fires — but the Worker's reading is stale and
    that is now said out loud."""
    await _prices(db, TODAY, tickers=A.SIGNAL_TICKERS)
    await _macro_series(db, "M2SL", TODAY - timedelta(days=66), spacing=31, n=13)
    assert await A.signal_freshness_alert(db, TODAY) is None
    macro = await A.macro_freshness_alert(db, TODAY)
    assert macro is not None and macro.level == "warn"


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


# -- the fourth verdict, surfaced ------------------------------------------


async def _measurement_event(db: InvestmentDB, verdict: str, traded: str | None = None) -> None:
    async with db.transaction():
        await db.append_event(
            type=W.RULE_MEASUREMENT_EVENT,
            source_uc="UC8",
            source_id="strat-rev",
            payload={
                "title": "shorten the trend window",
                # A REAL knob name. This said `ma_window_days`, which stopped
                # existing when the overlay became graduated (it is `ma_windows`,
                # a list, since 2026-08-14) — a fixture describing a revision the
                # registry could not measure, passing because an alert renders
                # whatever the payload holds.
                "overrides": {"ma_windows": [125, 250]},
                "verdict": verdict,
                "traded": traded,
            },
            event_date=TODAY,
        )


async def test_no_measurement_yet_says_nothing(db: InvestmentDB) -> None:
    assert await A.rule_tradeoff_alert(db) is None


async def test_an_adopted_or_rejected_measurement_says_nothing(db: InvestmentDB) -> None:
    """Only the verdict the machine WILL NOT decide needs a human. An adopt is
    already recorded and a reject already closed its strategy; repeating either
    in the digest is how a line stops being read."""
    await _measurement_event(db, "adopt")
    assert await A.rule_tradeoff_alert(db) is None
    await _measurement_event(db, "reject")
    assert await A.rule_tradeoff_alert(db) is None


async def test_a_trade_off_reaches_the_owner_with_its_exchange(db: InvestmentDB) -> None:
    """The case that forced the fourth verdict: a 125-day window buys 2.75pp
    of max drawdown for 0.94% of Sortino — the largest safety gain ever measured
    on this stack — and Pareto refuses it. Before 2026-08-13 that refusal was
    indistinguishable from "this made everything worse" and never left the log.

    The message must carry the EXCHANGE, not just the word: an owner deciding
    from the digest needs the terms, and the whole point is that they decide."""
    await _measurement_event(
        db, "trade-off", traded="buys max_drawdown +0.028 — costs sortino -0.011"
    )
    alert = await A.rule_tradeoff_alert(db)
    assert alert is not None
    assert alert.level == "warn"  # nothing is broken and nothing is blocked
    assert alert.code == "rule_tradeoff"
    assert "max_drawdown +0.028" in alert.message and "sortino -0.011" in alert.message
    assert "ma_windows" in alert.message


async def test_only_the_LATEST_measurement_is_raised(db: InvestmentDB) -> None:
    """A trade-off from two months ago has been decided or declined. Re-raising
    it every Sunday until someone acts is the failure mode `stack_drawdown_alert`
    documents from the other side."""
    await _measurement_event(
        db, "trade-off", traded="buys max_drawdown +0.028 — costs sortino -0.011"
    )
    await _measurement_event(db, "adopt")
    assert await A.rule_tradeoff_alert(db) is None
