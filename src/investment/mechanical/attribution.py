"""What the signal layer earns, and where the stack stands on the board.

THE MEASUREMENT THAT DID NOT EXIST. ADR-007 adopted the market-signal stack as
V1's live allocation path; `outcomes.strategy_probation_check` judges only
INNOVATION-born strategies (`source='agent-discovery'`), so `market-signal-stack`
— seeded from the corpus like the four bridge strategies — is the one strategy
the improvement cycle structurally cannot judge. `outcomes.py` says as much
where it declines to build `score_scenarios()`: "its live-path analog is
calibrating the MARKET-SIGNAL regime ... a job for the market-signal stack, not
this function". This is that job.

It DECIDES NOTHING. ADR-006's shape is measure -> propose -> maturation window
-> adopt/reject, and disabling the only live allocation path on a metric is a
far larger lever than the evidence here warrants. What was missing was not a
sanction, it was a number.

TWO READINGS, KEPT APART, because they answer different questions and a single
table would lose which is which:

  ATTRIBUTION — the stack against `ms-trend-baseline` ALONE. An A-B is
  interpretable only if B differs from A in exactly one thing, and the control
  arm is built that way: same overlay, same sleeves, same calendar
  (`market_signal.load_series` hands both arms one `StackSeries`), book frozen.
  `stack - spy` is not the signal layer — it is the signal plus the overlay plus
  the book choice plus the asset universe.

  BOARD STANDING — the stack against EVERY enabled portfolio. Measured because
  "the signal costs X against its control" and "the stack is 8th of 9 over a
  year" are different facts and neither implies the other. The attribution alone
  read far more harshly than the whole board supports: over ten years the stack
  carries a HIGHER Sortino than its control arm at an identical drawdown, having
  paid a point of CAGR for it, which is the trade the overlay exists to make.

NO PRE/POST SPLIT DATE. An earlier sketch cut the history at 2016 to show that
the signal's edge is old. A fixed cut is an invented parameter that drifts out
of date; TRAILING windows say the same thing and maintain themselves — a
positive `full` beside negative 1y/3y/5y/10y means the edge is older than the
longest trailing window, and the reader can see it without being told.

THE WINDOWS ARE COMMON, OR THE ROW IS NULL. Every portfolio is sliced to ONE
`as_of` and one window; a portfolio whose history does not reach the window
boundary reports None there rather than a short-window number that would silently
compare a 6-year CAGR with a 10-year one. `full` is the exception and is marked:
series begin on different dates, so it is a per-portfolio fact and never a
comparison — except for the attribution pair, whose two series share a calendar
by construction.

NOTHING IS RE-IMPLEMENTED. Every figure comes from `ratios`' pinned formulas,
called with `window=len(slice)` so the rolling helper's last value IS the
whole-window value (CLAUDE.md: "two implementations must produce the same
numbers"). Note the consequence for readers comparing to the ranking: the
Sortino here is over the named window, while `portfolio.sortino_rolling` is over
the pinned 756-day one. Same formula, different windows, different numbers.
"""

import dataclasses
import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios, rule_revision
from investment.mechanical.market_signal import (
    STACK_PORTFOLIO_ID,
    TREND_BASELINE_PORTFOLIO_ID,
)

logger = logging.getLogger(__name__)

ATTRIBUTION_EVENT = "SignalAttributionEvent"

# Trailing calendar windows, reusing `ratios.RETURN_WINDOWS_DAYS`' own day
# counts for the three it already pins (365 / 1095 / 1826) so a 3-year window
# means the same number of days here as it does on `portfolio.return_3y`. 10y
# extends the same convention (1826 x 2); `None` is since-inception.
WINDOWS: dict[str, int | None] = {
    "1y": ratios.RETURN_WINDOWS_DAYS["return_1y"],
    "3y": ratios.RETURN_WINDOWS_DAYS["return_3y"],
    "5y": ratios.RETURN_WINDOWS_DAYS["return_5y"],
    "10y": ratios.RETURN_WINDOWS_DAYS["return_5y"] * 2,
    "full": None,
}

# THE WINDOWS THE ALERT JUDGES ON — every COMMON window of three years or more.
#
# 1y is measured and journalled but never alarmed on: one bad year is not a
# verdict about a strategy whose whole mechanism is slow. `full` is excluded for
# a different reason — series begin on different dates, so a portfolio that
# merely started later could "dominate" on a window nobody shares.
#
# A set rather than one window, because which ones fire is itself the finding:
# on 2026-08-28 the control arm dominates over 5y and 10y and does NOT over 3y,
# and collapsing that to a single yes/no would throw away the only part a reader
# can act on.
VERDICT_WINDOWS: tuple[str, ...] = ("3y", "5y", "10y")

# The four metrics a portfolio is compared on. Domination means beating the
# stack on ALL FOUR at once: a rank on any single metric is not a verdict, which
# is why the ranking rule itself sorts on three of them in order. On CAGR alone
# a decade-long bull run hands the answer to `spy-USD` every week, and an alert
# that fires every week is not read.
#
# HIGHER IS BETTER FOR ALL FOUR, including `max_drawdown` — it is stored as a
# NEGATIVE fraction (`min(NAV/cummax - 1)`), so -8.7% > -16.5% is both the
# arithmetic and the preference. Stated here rather than carried as a
# per-metric direction flag: a flag whose every value is the same is one the
# next reader has to prove is unused.
COMPARED_METRICS: tuple[str, ...] = ("cagr", "sortino", "calmar", "max_drawdown")

# How many weekly snapshots of the stack's rank the payload carries. BOUNDED
# because the event is append-only and this table grows forever; twelve is a
# quarter of standings, enough for "unchanged for N weeks" and small enough that
# the payload does not become the reason nobody opens it.
RANK_HISTORY_WEEKS = 12


@dataclasses.dataclass(frozen=True)
class WindowMetrics:
    """One portfolio over one window. Every field is None when the series does
    not cover the window — insufficient history, never a gap to fill."""

    cagr: float | None
    sortino: float | None
    calmar: float | None
    max_drawdown: float | None

    def complete(self) -> bool:
        """Whether this row can take part in a comparison at all. A partial row
        cannot be dominated or dominate: three metrics out of four is not a
        weaker verdict, it is a different question."""
        return all(getattr(self, name) is not None for name in COMPARED_METRICS)


@dataclasses.dataclass(frozen=True)
class AttributionResult:
    """What one run measured — returned for the caller and the tests, and
    journalled verbatim as the event payload."""

    as_of: str
    board: dict[str, dict[str, WindowMetrics]]  # portfolio_id -> window -> metrics
    attribution: dict[str, dict[str, float | None]]  # window -> metric -> stack minus control
    dominators: dict[str, list[str]]  # window -> portfolios beating the stack on all four
    rank_history: list[dict[str, Any]]  # [{date, rank, board_size}], oldest first
    verdict_windows: list[str]


# -- pure core --------------------------------------------------------------


def window_slice(nav: pd.Series, as_of: pd.Timestamp, calendar_days: int | None) -> pd.Series:
    """The trailing window ending at `as_of`, or the whole series when
    `calendar_days is None`.

    EMPTY WHEN THE HISTORY IS SHORT, deliberately: if the series begins after
    the window boundary there is no 10-year figure to report, and returning the
    6 years that do exist would put a 6-year CAGR in a column headed 10y.
    `cumulative_return` takes the same stance one function over."""
    covered = nav[nav.index <= as_of]
    if calendar_days is None:
        return covered
    start = as_of - pd.Timedelta(days=calendar_days)
    if covered.empty or covered.index[0] > start:
        return pd.Series(dtype=float)
    return covered[covered.index >= start]


def window_metrics(nav: pd.Series, rf: pd.Series) -> WindowMetrics:
    """The four pinned indicators over the WHOLE of `nav`.

    `window=len(nav)` on each rolling helper makes its last value the
    whole-window value — the rolling machinery is the pinned formula, so this
    reuses it rather than writing a second one that would have to be proved
    equal (CLAUDE.md 'Mechanical calculations')."""
    if len(nav) < 2:
        return WindowMetrics(None, None, None, None)
    span = len(nav)
    returns = ratios.daily_returns(nav)
    sortino = ratios.rolling_sortino(returns, rf, span)
    max_drawdown = ratios.rolling_max_drawdown(nav, span)
    calmar = ratios.rolling_calmar(nav, max_drawdown, span)
    return WindowMetrics(
        cagr=ratios.cagr(nav),
        sortino=ratios.flt(sortino.iloc[-1]),
        calmar=ratios.flt(calmar.iloc[-1]),
        max_drawdown=ratios.flt(max_drawdown.iloc[-1]),
    )


def dominators(board: Mapping[str, WindowMetrics], subject: str) -> list[str]:
    """Portfolios that PARETO-DOMINATE `subject` on this window: no metric
    worse, at least one better. The only reading under which the stack has no
    defence left, and therefore the only one worth waking the owner for.

    PARETO, NOT "STRICTLY BETTER ON ALL FOUR", and the difference is not
    academic here. `ms-trend-baseline` reaches the stack's own covid trough by
    construction (`market_signal.TREND_BASELINE_BOOK`: "reproduces the stack's
    max drawdown to four decimals"), so their drawdowns are EQUAL on every long
    window. Requiring strict betterness on all four made that tie a permanent
    defence and the control arm undominatable — the stack could be worse on
    three metrics out of four and the rule would report nothing. It is also the
    verdict vocabulary `rule_revision` already uses on this stack ("the Pareto
    test only decides revisions where nothing gets worse").

    `rule_revision.direction` does the comparing, so the tie is decided by the
    MEASURED noise floor rather than by `==` on floats that differ at 2.2e-16 —
    which is the literal delta that trough produces (see `NOISE_REL_TOL`)."""
    target = board.get(subject)
    if target is None or not target.complete():
        return []
    beaten = []
    for portfolio_id, metrics in board.items():
        if portfolio_id == subject or not metrics.complete():
            continue
        directions = [
            rule_revision.direction(float(getattr(target, name)), float(getattr(metrics, name)))
            for name in COMPARED_METRICS
        ]
        if -1 not in directions and 1 in directions:
            beaten.append(portfolio_id)
    return sorted(beaten)


def attribution_deltas(
    stack: Mapping[str, WindowMetrics], control: Mapping[str, WindowMetrics]
) -> dict[str, dict[str, float | None]]:
    """Stack MINUS control arm, per window and per metric — what the signal
    layer bought or cost.

    Valid on EVERY window including `full`, unlike the board comparison: the two
    arms are built from one `StackSeries` and therefore share a calendar by
    construction, so no window can be an artefact of a different start date
    (`market_signal.TREND_BASELINE_PORTFOLIO_ID` states the same property from
    the other side)."""
    deltas: dict[str, dict[str, float | None]] = {}
    for window in WINDOWS:
        a, b = stack.get(window), control.get(window)
        if a is None or b is None:
            continue
        deltas[window] = {
            name: (
                None
                if getattr(a, name) is None or getattr(b, name) is None
                else getattr(a, name) - getattr(b, name)
            )
            for name in COMPARED_METRICS
        }
    return deltas


# -- DB-facing --------------------------------------------------------------


async def _common_as_of(
    db: InvestmentDB, portfolio_ids: Sequence[str]
) -> tuple[pd.Timestamp, list[str]] | None:
    """The latest date every PRICED portfolio has a NAV row for, and which
    portfolios those are. None only when not one of them has a NAV at all.

    The earliest of the last dates, not the latest: one date for the whole
    board or the comparison is between two different days, which is the defect
    the 2026-08-30 chain reorder fixed one layer down. `ratios.value_portfolios`
    now warns when these diverge; this one silently agrees with the laggard,
    because a comparison is worth more on a slightly older common date than on a
    fresh split one.

    A PORTFOLIO WITH NO NAV AT ALL IS DROPPED, NOT A REASON TO MEASURE NOTHING
    (2026-09-06). It was: any enabled portfolio missing from `portfolio_nav`
    made this return None and skipped the whole run on a `logger.info`. That is
    reachable on an ordinary database — `weekly.py` documents that the `aaaf-r`
    step "warns and skips on a missing series by design", so `aaaf-r-USD` can
    sit enabled with zero NAV rows — and the cost was silent and permanent: the
    ADOPTED strategy's only measurement simply never runs again, while the
    digest keeps printing the last event it did write. `ratios.value_portfolios`
    sets the precedent, skipping the portfolio rather than the valuation, and
    dropping it costs the comparison nothing: with no NAV every window of it is
    None and `WindowMetrics.complete()` already refuses it a verdict."""
    placeholders = ", ".join(f":p{i}" for i in range(len(portfolio_ids)))
    rows = await db.query(
        f"SELECT portfolio_id, MAX(ts) AS last_ts FROM portfolio_nav "
        f"WHERE portfolio_id IN ({placeholders}) GROUP BY portfolio_id",
        **{f"p{i}": pid for i, pid in enumerate(portfolio_ids)},
    )
    if not rows:
        return None
    priced = sorted(str(row["portfolio_id"]) for row in rows)
    missing = sorted(set(portfolio_ids) - set(priced))
    if missing:
        logger.warning("signal attribution: enabled with no NAV, off the board: %s", missing)
    return min(pd.Timestamp(str(row["last_ts"])) for row in rows), priced


async def measure_board(
    db: InvestmentDB, portfolio_ids: Sequence[str], as_of: pd.Timestamp
) -> dict[str, dict[str, WindowMetrics]]:
    """Every portfolio, every window, on one `as_of`."""
    rf = await ratios.load_rf_daily(db)
    board: dict[str, dict[str, WindowMetrics]] = {}
    for portfolio_id in portfolio_ids:
        nav = await ratios.load_nav(db, portfolio_id)
        board[portfolio_id] = {
            label: window_metrics(window_slice(nav, as_of, days), rf)
            for label, days in WINDOWS.items()
        }
    return board


async def rank_history(db: InvestmentDB, portfolio_id: str, weeks: int) -> list[dict[str, Any]]:
    """The stack's rank in the last `weeks` weekly snapshots, oldest first.

    READ, never recomputed: `portfolio_weekly_snapshot` already stores a rank
    per enabled portfolio per week, so the longitudinal half of this job is a
    query. What was missing was never the measurement — it was that nobody read
    it across weeks."""
    rows = await db.query(
        "SELECT date, rank, (SELECT COUNT(*) FROM portfolio_weekly_snapshot i "
        "  WHERE i.date = o.date) AS board_size "
        "FROM portfolio_weekly_snapshot o WHERE portfolio_id = :pid "
        "ORDER BY date DESC LIMIT :n",
        pid=portfolio_id,
        n=weeks,
    )
    return [
        {"date": str(r["date"]), "rank": int(r["rank"]), "board_size": int(r["board_size"])}
        for r in reversed(rows)
    ]


async def run_signal_attribution(
    db: InvestmentDB, today: date | None = None
) -> AttributionResult | None:
    """Measure and journal. Returns None when there is nothing to measure yet
    (no stack NAV, no control arm) rather than writing an empty event.

    Runs AFTER `ranking`: it reads the snapshot table that step writes, and the
    NAV the refresh block leaves. One `SignalAttributionEvent` per run, whether
    or not anything is worth alarming about — the point is the record, and a
    measurement written only when it is bad is a measurement nobody can trend."""
    today = today or date.today()
    enabled = [
        str(row["id"]) for row in await db.query("SELECT id FROM portfolio WHERE enabled = 1")
    ]
    if STACK_PORTFOLIO_ID not in enabled or TREND_BASELINE_PORTFOLIO_ID not in enabled:
        logger.info("signal attribution skipped: stack or control arm not enabled")
        return None
    common = await _common_as_of(db, enabled)
    if common is None:
        logger.info("signal attribution skipped: no enabled portfolio has a NAV")
        return None
    as_of, priced = common
    # The two ARMS are the one thing that cannot be dropped: without both there
    # is no attribution to compute, only a board standing.
    if STACK_PORTFOLIO_ID not in priced or TREND_BASELINE_PORTFOLIO_ID not in priced:
        logger.info("signal attribution skipped: stack or control arm has no NAV")
        return None

    board = await measure_board(db, priced, as_of)
    result = AttributionResult(
        as_of=as_of.date().isoformat(),
        board=board,
        attribution=attribution_deltas(
            board[STACK_PORTFOLIO_ID], board[TREND_BASELINE_PORTFOLIO_ID]
        ),
        # `full` is excluded: series start on different dates, so a portfolio
        # that simply began later would "dominate" on a window nobody shares.
        dominators={
            window: dominators(
                {pid: windows[window] for pid, windows in board.items()}, STACK_PORTFOLIO_ID
            )
            for window in WINDOWS
            if window != "full"
        },
        rank_history=await rank_history(db, STACK_PORTFOLIO_ID, RANK_HISTORY_WEEKS),
        verdict_windows=list(VERDICT_WINDOWS),
    )
    async with db.transaction():
        await db.append_event(
            type=ATTRIBUTION_EVENT,
            source_uc="UC7",
            source_id=STACK_PORTFOLIO_ID,
            payload=dataclasses.asdict(result),
            event_date=today,
        )
    logger.info(
        "signal attribution as of %s: dominators %s",
        result.as_of,
        {w: result.dominators.get(w, []) for w in VERDICT_WINDOWS},
    )
    return result
