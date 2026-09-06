"""What the signal layer earns, and where the stack stands
(`mechanical/attribution.py`).

Real throwaway SQLite with synthetic NAV, no mocks (CLAUDE.md "Tests"). The
series are built to make ONE property visible per test rather than to look like
markets: a comparison rule is what is under test here, not a return.
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical import attribution as A
from investment.mechanical.market_signal import (
    STACK_PORTFOLIO_ID,
    TREND_BASELINE_PORTFOLIO_ID,
)

TODAY = date(2026, 8, 30)
AS_OF = pd.Timestamp("2026-08-28")


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "attribution.db")
    yield conn
    await conn.close()


def _series(start: str, days: int, daily: float) -> pd.Series:
    """A NAV compounding at `daily` with a small regular dip.

    THE DIP IS NOT DECORATION. A strictly monotone NAV has zero downside
    deviation and zero drawdown, so Sortino divides by zero and Calmar divides
    by zero — every metric comes back inf or NaN, `WindowMetrics.complete()` is
    False and nothing can be compared at all. The fixture has to produce a book
    that CAN lose, because the rule under test is about losing."""
    index = pd.bdate_range(start=start, periods=days)
    values = [100.0 * (1.0 + daily) ** i * (0.97 if i % 20 == 0 else 1.0) for i in range(days)]
    return pd.Series(values, index=index)


async def _risk_free(db: InvestmentDB, index: pd.DatetimeIndex) -> None:
    """`ratios.load_rf_daily` reads ^IRX, and Sortino is an EXCESS-return
    statistic: with no rf series every excess return is NaN and every metric is
    None. Seeded flat, so the tests measure the comparison rule and not a
    moving risk-free rate."""
    await db.append_ts_batch(
        "market_data",
        [
            {
                "ts": ts.date().isoformat(),
                "ticker": "^IRX",
                "asset_class": "MACRO",
                "currency": "USD",
                "level": 2.0,
            }
            for ts in index
        ],
    )


async def _framework(db: InvestmentDB) -> None:
    """`portfolio.framework_id` is a real FK, so the vertex needs its framework
    before it exists — the schema is the authority here, not the fixture."""
    await db.command(
        "INSERT OR IGNORE INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('f', 'f', 1, 't', :now)",
        now=TODAY.isoformat(),
    )


async def _portfolio(db: InvestmentDB, portfolio_id: str, nav: pd.Series) -> None:
    await _framework(db)
    await db.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) "
        "VALUES (:id, :id, 'f', 0, 1, 'USD', 'b', '{}', -25.0, 60.0, 'accumulation', 't', :now)",
        id=portfolio_id,
        now=TODAY.isoformat(),
    )
    await _risk_free(db, pd.DatetimeIndex(nav.index))
    await db.append_ts_batch(
        "portfolio_nav",
        [
            {
                "portfolio_id": portfolio_id,
                "currency": "USD",
                "ts": ts.date().isoformat(),
                "nav": float(value),
            }
            for ts, value in nav.items()
        ],
    )


# -- the window rule --------------------------------------------------------


def test_a_window_the_history_does_not_cover_is_empty_not_short() -> None:
    """The whole reason `WindowMetrics` can be None. A series starting six years
    ago has no 10-year figure, and returning its six years would put a six-year
    CAGR in a column headed 10y — the exact class of error the 2026-08-30 chain
    fix was about, one dimension over."""
    nav = _series("2020-08-28", 1500, 0.0002)
    assert A.window_slice(nav, AS_OF, A.WINDOWS["10y"]).empty
    covered = A.window_slice(nav, AS_OF, A.WINDOWS["3y"])
    assert not covered.empty
    assert covered.index[0] >= AS_OF - pd.Timedelta(days=A.WINDOWS["3y"] or 0)


def test_full_window_is_the_whole_series_and_stops_at_as_of() -> None:
    nav = _series("2000-01-03", 7000, 0.0002)
    full = A.window_slice(nav, AS_OF, None)
    assert full.index[0] == nav.index[0]
    assert full.index[-1] <= AS_OF


def test_metrics_come_from_the_pinned_formulas() -> None:
    """`window_metrics` must not be a second implementation: the same slice run
    through `ratios` by hand has to give the same four numbers (CLAUDE.md
    'Mechanical calculations')."""
    from investment.mechanical import ratios

    nav = _series("2016-01-04", 2600, 0.0003)
    rf = pd.Series(1e-5, index=nav.index)
    measured = A.window_metrics(nav, rf)
    span = len(nav)
    drawdown = ratios.rolling_max_drawdown(nav, span)
    assert measured.cagr == ratios.cagr(nav)
    assert measured.max_drawdown == ratios.flt(drawdown.iloc[-1])
    assert measured.calmar == ratios.flt(ratios.rolling_calmar(nav, drawdown, span).iloc[-1])
    assert measured.sortino == ratios.flt(
        ratios.rolling_sortino(ratios.daily_returns(nav), rf, span).iloc[-1]
    )


# -- the comparison rule ----------------------------------------------------


def test_a_tie_on_drawdown_does_not_save_the_stack() -> None:
    """THE CASE THAT FORCED PARETO. `ms-trend-baseline` reaches the stack's own
    covid trough by construction, so their drawdowns are equal on every long
    window. Under "strictly better on all four" that tie was a permanent
    defence and the control arm could never be reported as dominating, however
    far behind the stack fell on the other three."""
    board = {
        STACK_PORTFOLIO_ID: A.WindowMetrics(
            cagr=0.09, sortino=1.09, calmar=0.57, max_drawdown=-0.165
        ),
        TREND_BASELINE_PORTFOLIO_ID: A.WindowMetrics(
            cagr=0.103, sortino=1.11, calmar=0.62, max_drawdown=-0.165
        ),
    }
    assert A.dominators(board, STACK_PORTFOLIO_ID) == [TREND_BASELINE_PORTFOLIO_ID]


def test_float_noise_on_one_metric_is_not_a_verdict() -> None:
    """The literal delta the two arms produce at that trough is -2.2e-16
    (`rule_revision.NOISE_REL_TOL` records it). A rival that is better ONLY by
    that much has improved nothing, so there is no domination to report — and
    the floor deciding it is the measured one, shared with `rule_revision`,
    never a second epsilon invented here."""
    stack = A.WindowMetrics(cagr=0.09, sortino=1.09, calmar=0.57, max_drawdown=-0.20612458912985732)
    twin = A.WindowMetrics(cagr=0.09, sortino=1.09, calmar=0.57, max_drawdown=-0.2061245891298571)
    assert A.dominators({STACK_PORTFOLIO_ID: stack, "twin": twin}, STACK_PORTFOLIO_ID) == []


def test_better_on_two_and_worse_on_two_is_not_domination() -> None:
    """A trade-off is not a defeat. Over 3 years the live stack gives up CAGR
    and Sortino while buying Calmar and drawdown, and an alert that called that
    "dominated" would be reporting the overlay doing its job."""
    board = {
        STACK_PORTFOLIO_ID: A.WindowMetrics(cagr=0.15, sortino=1.5, calmar=1.1, max_drawdown=-0.08),
        "rival": A.WindowMetrics(cagr=0.21, sortino=1.7, calmar=0.9, max_drawdown=-0.12),
    }
    assert A.dominators(board, STACK_PORTFOLIO_ID) == []


def test_a_partial_row_neither_dominates_nor_is_dominated() -> None:
    """Three metrics out of four is a different question, not a weaker verdict
    — a portfolio too young for the window must not win it by default."""
    board = {
        STACK_PORTFOLIO_ID: A.WindowMetrics(cagr=0.09, sortino=1.0, calmar=0.5, max_drawdown=-0.2),
        "young": A.WindowMetrics(cagr=0.30, sortino=None, calmar=2.0, max_drawdown=-0.05),
    }
    assert A.dominators(board, STACK_PORTFOLIO_ID) == []


def test_attribution_is_stack_minus_control_arm() -> None:
    """Sign convention, asserted because every reader of the digest line depends
    on it: POSITIVE means the signal layer earned something."""
    stack = {"10y": A.WindowMetrics(cagr=0.09, sortino=1.0, calmar=0.5, max_drawdown=-0.20)}
    control = {"10y": A.WindowMetrics(cagr=0.10, sortino=0.9, calmar=0.6, max_drawdown=-0.25)}
    deltas = A.attribution_deltas(stack, control)["10y"]
    assert deltas["cagr"] == pytest.approx(-0.01)
    assert deltas["sortino"] == pytest.approx(0.10)
    assert deltas["max_drawdown"] == pytest.approx(0.05)


# -- the job ----------------------------------------------------------------


async def test_the_board_is_measured_on_one_common_as_of(db: InvestmentDB) -> None:
    """ONE DATE FOR THE WHOLE BOARD, and it is the EARLIEST of the last dates.
    A comparison is worth more on a slightly older shared date than on a fresh
    split one — which is the same lesson as the chain reorder that put the NAV
    producers ahead of their readers."""
    await _portfolio(db, STACK_PORTFOLIO_ID, _series("2000-01-03", 6900, 0.0002))
    await _portfolio(db, TREND_BASELINE_PORTFOLIO_ID, _series("2000-01-03", 6900, 0.0002))
    lagging = _series("2000-01-03", 6900, 0.0002).iloc[:-5]
    await _portfolio(db, "laggard", lagging)

    ids = [STACK_PORTFOLIO_ID, TREND_BASELINE_PORTFOLIO_ID, "laggard"]
    common = await A._common_as_of(db, ids)
    assert common is not None
    as_of, priced = common
    assert as_of.date().isoformat() == lagging.index[-1].date().isoformat()
    assert priced == sorted(ids)


async def test_an_enabled_portfolio_with_no_nav_leaves_the_board_not_the_run(
    db: InvestmentDB,
) -> None:
    """IT USED TO SKIP EVERYTHING, silently and forever. Any enabled portfolio
    absent from `portfolio_nav` made `_common_as_of` return None, and the
    ADOPTED strategy's only measurement stopped running — on a `logger.info`,
    while the digest kept printing the last event it had written. `weekly.py`
    documents the `aaaf-r` step as warning and skipping on a missing series BY
    DESIGN, so an enabled portfolio with no NAV is an ordinary state of this
    database, not a corruption.

    Dropping it costs the comparison nothing: with no NAV every window of it is
    None and `WindowMetrics.complete()` already refuses it a verdict."""
    await _portfolio(db, STACK_PORTFOLIO_ID, _series("2000-01-03", 6900, 0.00020))
    await _portfolio(db, TREND_BASELINE_PORTFOLIO_ID, _series("2000-01-03", 6900, 0.00030))
    await db.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) "
        "VALUES ('navless', 'navless', 'f', 0, 1, 'USD', 'b', '{}', -25.0, 60.0, "
        "'accumulation', 't', :now)",
        now=TODAY.isoformat(),
    )

    result = await A.run_signal_attribution(db, today=TODAY)
    assert result is not None
    assert "navless" not in result.board
    assert result.dominators["10y"] == [TREND_BASELINE_PORTFOLIO_ID]


async def test_the_two_arms_are_the_one_thing_that_cannot_be_dropped(db: InvestmentDB) -> None:
    """The board standing survives a missing portfolio; the ATTRIBUTION cannot.
    A control arm with no NAV is not a smaller measurement, it is a different
    one — so the run is skipped rather than journalled without its A-B."""
    await _portfolio(db, STACK_PORTFOLIO_ID, _series("2000-01-03", 6900, 0.00020))
    await _portfolio(db, "other", _series("2000-01-03", 6900, 0.00030))
    await db.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) "
        "VALUES (:id, :id, 'f', 0, 1, 'USD', 'b', '{}', -25.0, 60.0, 'accumulation', 't', :now)",
        id=TREND_BASELINE_PORTFOLIO_ID,
        now=TODAY.isoformat(),
    )

    assert await A.run_signal_attribution(db, today=TODAY) is None
    assert await db.query("SELECT id FROM event_log") == []


async def test_a_run_journals_one_event_and_names_the_dominator(db: InvestmentDB) -> None:
    """END TO END: the control arm compounds faster at the same (zero) drawdown,
    so it Pareto-dominates on every common window, and the run says so in one
    `SignalAttributionEvent`."""
    await _portfolio(db, STACK_PORTFOLIO_ID, _series("2000-01-03", 6900, 0.00020))
    await _portfolio(db, TREND_BASELINE_PORTFOLIO_ID, _series("2000-01-03", 6900, 0.00030))

    result = await A.run_signal_attribution(db, today=TODAY)
    assert result is not None
    for window in A.VERDICT_WINDOWS:
        assert result.dominators[window] == [TREND_BASELINE_PORTFOLIO_ID]
    assert result.attribution["full"]["cagr"] is not None
    assert result.attribution["full"]["cagr"] < 0  # the signal layer lost ground

    events = await db.query(
        "SELECT type, source_id FROM event_log WHERE type = :t", t=A.ATTRIBUTION_EVENT
    )
    assert len(events) == 1
    assert events[0]["source_id"] == STACK_PORTFOLIO_ID


async def test_the_run_is_skipped_rather_than_journalled_empty(db: InvestmentDB) -> None:
    """No control arm, nothing to attribute. Writing an event anyway would put a
    row in an append-only log saying a measurement happened when none did."""
    await _portfolio(db, STACK_PORTFOLIO_ID, _series("2020-01-01", 1500, 0.0002))
    assert await A.run_signal_attribution(db, today=TODAY) is None
    assert await db.query("SELECT id FROM event_log") == []


async def test_rank_history_is_read_from_the_snapshot_and_bounded(db: InvestmentDB) -> None:
    """The longitudinal half is a QUERY: `portfolio_weekly_snapshot` already
    stores a rank per portfolio per week, and what was missing was never the
    measurement — it was that nobody read it across weeks. Bounded because the
    event is append-only and the table grows forever."""
    rows = []
    for week in range(20):
        stamp = (date(2026, 1, 4) + timedelta(weeks=week)).isoformat()
        for rank, pid in enumerate([STACK_PORTFOLIO_ID, "other"], start=1):
            rows.append(
                {
                    "date": stamp,
                    "portfolio_id": pid,
                    "defender": 0,
                    "framework_id": "f",
                    "allocation": "{}",
                    "rank": rank,
                    "market_context": "{}",
                    "recommendation": "maintain",
                    "trace": "t",
                }
            )
    for row in rows:
        columns = ", ".join(row)
        await db.command(
            f"INSERT INTO portfolio_weekly_snapshot ({columns}) "
            f"VALUES ({', '.join(':' + c for c in row)})",
            **row,
        )
    history = await A.rank_history(db, STACK_PORTFOLIO_ID, A.RANK_HISTORY_WEEKS)
    assert len(history) == A.RANK_HISTORY_WEEKS
    assert history[0]["date"] < history[-1]["date"]  # oldest first
    assert history[-1]["rank"] == 1
    assert history[-1]["board_size"] == 2
