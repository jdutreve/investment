"""The market-signal monthly stack — V1's ADOPTED allocation (ADR-007).

A countercyclical, market-priced strategy after Verdad/Rasmussen (the origin of
the approach; docs/V1_STRATEGY.md carries the attribution). Named neutrally here.

The strategy the pivot adopted (docs/V1_STRATEGY.md): a market-priced,
CONTEMPORANEOUS regime read (credit spread + yield slope, no CPI/GDP lag) picks
one of three CONCENTRATED books, and a 200-day trend-following overlay redirects
the equity/gold sleeves to intermediate Treasuries when they are below trend.
Decision cadence is MONTHLY (docs/V1_STRATEGY.md "Why monthly").

ANTI-DRIFT (the point of this module): the numbers that earned the pivot — 9.85%
CAGR / -24% daily max drawdown, +2.5 vs B, robust in AND out of sample — came
out of a scratchpad backtest (`global_table_daily.py`, the "signal+trend"
line). This is that logic ported verbatim onto the SAME NAV engine the backtest
used (`replay.shadow_book_nav`, itself pinned equal to the M4-validated
`ratios.synthesize_nav`), so wiring the stack cannot silently diverge from the
figures ADR-007 was signed on.

Those figures have been superseded TWICE, both times deliberately and
owner-arbitrated:
- 2026-08-01, `CONFIRM_DECISIONS` hysteresis: 9.85% -> 11.26% CAGR, Sortino
  0.94 -> 1.11, drawdown unchanged;
- 2026-08-02, ADR-010's single cost rate: 11.26% -> **11.14%**, Sortino 1.09,
  drawdown still -23.8%. Two disagreeing guesses were replaced by one measured
  rate: the stack had been charged 20 bps PER SIDE while `replay_cost_bps` said
  10 and the spec claimed "20 bps/rotation", and every static book it is ranked
  against paid nothing at all. Now Saxo's real 23 bps/order (no FX — every
  portfolio is USD in a USD account) bills all of them, drift-rebalance
  included.

The pinned pair is therefore **11.14% / -23.8%**. The earlier figures are
history, not targets. Any OTHER divergence from 11.14% is drift and must be
explained, which is what this module exists to guarantee.

PURE decision logic (`classify_regime`, `apply_trend_overlay`,
`advance_hysteresis`, `walk_decisions`) takes already-loaded series and holds no
I/O — the same separation as `mechanical/gates.py`, so the classifier is
unit-testable without a DB. `run_market_signal` is the thin I/O driver.

`walk_decisions` is the SINGLE decision clock: it emits one `Decision` per
monthly decision date, and BOTH consumers derive from it — the replay via
`build_targets` (which keeps only the change points `shadow_book_nav` wants) and
the LIVE monthly path (`market_signal_cycle.py`) via the last entry of the same
walk run with `end=today`. That is how the live path "calls the identical
function the replay validates": not a shared helper, the shared WALK. A live
decision that disagreed with the backtest would have to disagree with itself.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import date

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios, replay
from investment.mechanical.gates import Caps, concentration_ok, drawdown_ok
from investment.mechanical.replay import NavMetrics, nav_metrics, shadow_book_nav

# The 3 books (docs/V1_STRATEGY.md). Concentrated tilts: the 50% sleeves are the
# measured source of the +2.5-vs-B edge and the reason the single-asset cap was
# raised 40 -> 50 (ADR-007 addendum). Weights are allocation percent points.
#
# NAMED AFTER THE SIGNAL STATE THAT SELECTS THEM, not after a macro regime
# (renamed 2026-07-20, ADR-007 addendum; previously growth/inflation/slowdown).
# The old names asserted a macro reading the books do not have: measured over
# the 418 monthly decisions, the market signal is essentially ORTHOGONAL to CPI
# — each book spent 28-33% of its time with CPI YoY above 3% against a 31.3%
# base rate, and the book then called "inflation" averaged CPI 2.99 vs 2.23 for
# the one called "growth" (docs/IMPROVEMENTS.md I-39). Since the Worker is an
# LLM that reads these keys as semantic context, a book called "inflation" that
# does not track inflation is a reasoning hazard, not just untidy naming.
BOOKS: dict[str, dict[str, float]] = {
    "credit-spread-wide": {"SPY": 50.0, "IWN": 40.0, "GLD": 10.0},
    "credit-spread-tight-yield-curve-flat": {"SPY": 50.0, "GLD": 40.0, "IWN": 10.0},
    "credit-spread-tight-yield-curve-steep": {"VCIT": 50.0, "IEF": 40.0, "IWN": 10.0},
}

# The 200d trend overlay: these sleeves are redirected to TREND_HAVEN when their
# own price is below their 200-day moving average. This is the drawdown control
# (-24% with it, -50% without — docs/V1_STRATEGY.md).
TREND_SLEEVES: tuple[str, ...] = ("SPY", "GLD")
TREND_HAVEN = "IEF"

# The STACK itself as a Portfolio vertex — the object that is actually held, as
# opposed to the 3 books, which are only ever held conditionally and always
# through the overlay. It exists so the stack has a `portfolio_nav` series like
# every other portfolio: without one, its 36M rolling drawdown (the measure the
# -25% cap is about) cannot be computed at all, and the ranking compares three
# static fictions nobody holds instead of the one thing that is (ADR-009).
STACK_PORTFOLIO_ID = "ms-stack"

# Decision key -> the seeded Portfolio vertex that IS that book (db/seed_data.py
# PORTFOLIOS). The live path needs it because `Proposal.defender_id` is a
# portfolio id, not a signal state. The entity ids keep their original
# growth/inflation/slowdown spelling: they already appear in committed EventLog
# payloads, which are append-only (seed_data's ms-growth-book note). This map is
# the ONE place the frozen spelling meets the renamed decision keys, so no other
# module has to know both vocabularies.
BOOK_PORTFOLIO_IDS: dict[str, str] = {
    "credit-spread-wide": "ms-growth-book",
    "credit-spread-tight-yield-curve-flat": "ms-inflation-book",
    "credit-spread-tight-yield-curve-steep": "ms-slowdown-book",
}

# The market-signal series and their trailing-median lookbacks. ~10y median
# (2520 trading days) with a 1y warm-up floor, matching the backtest.
CREDIT_SPREAD = "BAA10Y"
YIELD_SLOPE = "T10Y2Y"
MEDIAN_WINDOW_DAYS = 2520
MEDIAN_MIN_DAYS = 252
MA_WINDOW_DAYS = 200

# Every ticker any book can hold — what `run_market_signal` must load prices for. The
# bug that once crippled this stack (docs/STRATEGY_COMPARISON.md correction note)
# was loading a prices dict MISSING IWN/VCIT, which then held flat at 0%; naming
# the set here makes that omission impossible to repeat silently.
STACK_TICKERS: tuple[str, ...] = ("SPY", "IWN", "GLD", "VCIT", "IEF")

# The stack is charged at the SAME per-order rate as every other NAV in the
# system (ADR-010): Saxo's real 23 bps. Was 20 here — which happened to be
# double `replay_cost_bps` AND double the "20 bps/rotation" the spec claimed,
# while every static book it is ranked against paid nothing.
COST_BPS = ratios.TRADING_COST_BPS

# Consecutive monthly decisions that must name the SAME new book before the
# stack switches (measured 2026-08-01, full 35y + split sample).
#
# `classify_regime` is a bare comparison against a trailing median, so a signal
# hovering at its own median flips the book on an arbitrarily small difference:
# of the 36 book changes over 409 monthly decisions, 25% were decided by a
# margin under 2% and 14 reversed within 3 months. Books barely overlap (wide is
# SPY/IWN/GLD, steep is VCIT/IEF/IWN), so such a flip is close to a 90% round
# trip. Waiting for confirmation lifts CAGR 9.85% -> 11.26% and Sortino
# 0.94 -> 1.11 at an UNCHANGED -23.8% drawdown, in both halves of the history
# split independently; the sweep degrades past ~4, so this is a real optimum,
# not "trade less" (buy-and-hold scores 10.32% CAGR but -52% drawdown).
#
# 3 is `regime_confirm_prints`' value, deliberately: one hysteresis convention
# across the project. A separate constant, NOT a read of that threshold —
# recalibrating the macro detector must never silently move the allocation.
# Holding the two candidate books during the wait (the literal "intersection"
# proposal) measured WORSE than simply waiting, and raised turnover.
CONFIRM_DECISIONS = 3


@dataclasses.dataclass(frozen=True)
class TrendRead:
    """One trend sleeve's 200d overlay read at a decision date. Carries the two
    numbers the comparison was made on, not just its boolean answer: "SPY is
    below trend" is unauditable, "SPY 512.40 vs MA200 548.10" is."""

    price: float
    moving_average: float | None  # None before MA_WINDOW_DAYS of history
    below: bool


@dataclasses.dataclass(frozen=True)
class Decision:
    """ONE monthly decision, with everything that produced it.

    This is the audit record the live path persists (and the replay could): the
    raw signal state, the book actually HELD after hysteresis, the sleeves below
    their 200d MA, and the post-overlay target. `changed` is True iff the target
    differs from the previous decision's — i.e. iff this decision moves money.

    Medians are `None` during warm-up (before MEDIAN_MIN_DAYS of history), which
    is the state `classify_regime` answers with the credit-spread-wide default;
    keeping it None rather than NaN is what lets the whole record serialise
    straight into a Proposal's `market_context` as valid JSON."""

    date: pd.Timestamp
    signalled: str  # what the signal said THIS decision, before hysteresis
    held: str  # the book actually in force after hysteresis
    pending: str | None  # a different book waiting for confirmation
    pending_count: int  # consecutive decisions it has been waiting
    spread: float
    spread_median: float | None
    slope: float
    slope_median: float | None
    trend: dict[str, TrendRead]  # per TREND_SLEEVES sleeve
    target: dict[str, float]  # post-overlay effective allocation
    changed: bool

    @property
    def below_trend(self) -> tuple[str, ...]:
        """The sleeves the overlay redirected to TREND_HAVEN this decision."""
        return tuple(t for t, read in self.trend.items() if read.below)


@dataclasses.dataclass(frozen=True)
class MarketSignalRun:
    """A backtest/replay of the stack over a window.

    `targets` maps each CHANGE date -> the book that took effect (only dates
    where the allocation actually changed, matching `shadow_book_nav`'s
    time-varying target contract); `turnover` is its summed round-trip turnover.
    `decisions` is the FULL journal — every decision date, changed or not — which
    is what the live path needs: a decision that lands on the held book still
    advances the hysteresis counter and still has to be recorded.
    `raw_series` keeps each input UN-forward-filled, so the live path can report
    the date every input became knowable (ADR-003)."""

    nav: pd.Series
    targets: dict[pd.Timestamp, dict[str, float]]
    turnover: float
    decisions: list[Decision] = dataclasses.field(default_factory=list)
    raw_series: dict[str, pd.Series] = dataclasses.field(default_factory=dict)


# -- pure decision logic (no I/O — unit-testable, shared with the live path) --


def classify_regime(
    spread: float, spread_median: float | None, slope: float, slope_median: float | None
) -> str:
    """The market-signal regime (docs/V1_STRATEGY.md "Regime signal"):
    credit spread WIDE vs its 10y median -> `credit-spread-wide` (stress is
    PRICED, so the countercyclical response is to buy risk); else, on the
    slope: FLAT vs its 10y median -> `credit-spread-tight-yield-curve-flat`,
    STEEP -> `credit-spread-tight-yield-curve-steep`.

    The returned key names the SIGNAL STATE, not a macro regime — see BOOKS.
    Note the deliberate asymmetry the names encode: when the spread is WIDE the
    yield curve is never consulted, which is why that key carries no curve term.

    A missing median (warm-up, before MEDIAN_MIN_DAYS of history) defaults to
    `credit-spread-wide` — the equity-tilted book — exactly as the backtest did
    rather than stalling; the trend overlay still guards its downside."""
    if spread_median is None or pd.isna(spread_median) or spread > spread_median:
        return "credit-spread-wide"
    if slope_median is None or pd.isna(slope_median) or slope < slope_median:
        return "credit-spread-tight-yield-curve-flat"
    return "credit-spread-tight-yield-curve-steep"


def apply_trend_overlay(book: Mapping[str, float], below_trend: frozenset[str]) -> dict[str, float]:
    """Redirect each TREND_SLEEVES weight to TREND_HAVEN when that sleeve is
    below its 200d MA. Weights merge additively — if a book already holds
    TREND_HAVEN (the credit-spread-tight-yield-curve-steep book holds IEF), a
    redirected sleeve adds to it."""
    adjusted: dict[str, float] = {}
    for ticker, weight in book.items():
        destination = TREND_HAVEN if ticker in TREND_SLEEVES and ticker in below_trend else ticker
        adjusted[destination] = adjusted.get(destination, 0.0) + float(weight)
    return adjusted


def advance_hysteresis(
    held: str | None, pending: str | None, pending_count: int, signalled: str
) -> tuple[str, str | None, int]:
    """One step of the `CONFIRM_DECISIONS` hysteresis: the stack stays in the
    book it is committed to until a DIFFERENT book has been named that many
    decisions in a row. The first decision commits immediately (`held is None` —
    there is nothing to hold yet), and a candidate that flickers back resets the
    count. Returns the state AFTER the step: `(held, pending, pending_count)`.

    Pulled out of the walk so the state machine is one testable function rather
    than a loop body — and so the live path can state its carried-over state in
    the same three names it reads back from the previous decision."""
    if held is None or signalled == held:
        return signalled, None, 0
    count = pending_count + 1 if pending == signalled else 1
    if count >= CONFIRM_DECISIONS:
        return signalled, None, 0
    return held, signalled, count


def walk_decisions(
    dates: Sequence[pd.Timestamp],
    spread: pd.Series,
    slope: pd.Series,
    spread_median: pd.Series,
    slope_median: pd.Series,
    moving_averages: Mapping[str, pd.Series],
    prices: Mapping[str, pd.Series],
) -> list[Decision]:
    """Walk the decision clock and record EVERY decision — the full journal.

    The trend overlay is NOT damped: it re-reads the 200d MA on every decision,
    so the drawdown control keeps reacting while a book switch waits out its
    confirmation window."""
    decisions: list[Decision] = []
    previous: dict[str, float] | None = None
    held: str | None = None
    pending: str | None = None
    pending_count = 0
    for t in dates:
        signalled = classify_regime(
            _at(spread, t), _at(spread_median, t), _at(slope, t), _at(slope_median, t)
        )
        held, pending, pending_count = advance_hysteresis(held, pending, pending_count, signalled)
        trend = {
            ticker: _trend_read(_at(prices[ticker], t), _at(moving_averages[ticker], t))
            for ticker in TREND_SLEEVES
            if ticker in prices
        }
        # `ticker`, not `t` — `t` is the decision date in this scope, and while
        # a generator expression has its own, reusing the name here reads as a
        # shadow to anyone auditing the walk.
        book = apply_trend_overlay(
            BOOKS[held], frozenset(ticker for ticker, read in trend.items() if read.below)
        )
        decisions.append(
            Decision(
                date=t,
                signalled=signalled,
                held=held,
                pending=pending,
                pending_count=pending_count,
                spread=_at(spread, t),
                spread_median=_opt(_at(spread_median, t)),
                slope=_at(slope, t),
                slope_median=_opt(_at(slope_median, t)),
                trend=trend,
                target=book,
                changed=book != previous,
            )
        )
        previous = book
    return decisions


def build_targets(
    dates: Sequence[pd.Timestamp],
    spread: pd.Series,
    slope: pd.Series,
    spread_median: pd.Series,
    slope_median: pd.Series,
    moving_averages: Mapping[str, pd.Series],
    prices: Mapping[str, pd.Series],
) -> dict[pd.Timestamp, dict[str, float]]:
    """The change-point map `shadow_book_nav` consumes: a target ONLY on the
    dates the book actually changes (a monthly re-evaluation that lands on the
    same book pays no turnover). A pure projection of `walk_decisions` — the
    replay and the live path cannot drift because there is only one walk."""
    decisions = walk_decisions(
        dates, spread, slope, spread_median, slope_median, moving_averages, prices
    )
    return {d.date: d.target for d in decisions if d.changed}


def _at(series: pd.Series, t: pd.Timestamp) -> float:
    """Point read that tolerates a decision date off the series index (returns
    NaN), so `classify_regime`'s warm-up default fires instead of a KeyError."""
    value = series.get(t)
    return float("nan") if value is None else float(value)


def _trend_read(price: float, moving_average: float) -> TrendRead:
    """One sleeve's overlay read. A missing MA (warm-up) is NOT below trend —
    the overlay stays out of the way until it has 200 days to speak with, the
    same "unmeasured is not bad" rule `gates.drawdown_ok` applies."""
    ma = _opt(moving_average)
    return TrendRead(price=price, moving_average=ma, below=ma is not None and price < ma)


def _opt(value: float) -> float | None:
    """NaN -> None, so a warm-up median serialises as JSON `null` rather than
    the bare token `NaN`, which `json.loads` accepts but no other reader does."""
    return None if pd.isna(value) else value


# -- gate confrontation (the caps still BIND the adopted stack — CLAUDE.md) ---


def cap_violations(run: MarketSignalRun, caps: Caps, stack_drawdown: float | None) -> list[str]:
    """The binding-cap confrontation M6-bis's DoV asserts is empty. Every target
    book must clear the single-asset cap (now 50) EXCEPT the trend-haven sleeve,
    and the STACK's realized drawdown must clear the drawdown cap (now -25%,
    applied to the stack, not to each book standalone — ADR-007). Returns the
    failing gate names, [] if none.

    TREND_HAVEN is exempted from the single-asset cap (ADR-007 addendum,
    choice (a)): the overlay's flight-to-safety can pile both equity/gold sleeves
    into IEF (~90% in risk-off), which is the deliberate drawdown control, not a
    conviction bet. Uses the SAME `gates.py` predicate the live Writeback runs,
    with the same exemption, so a book blocked live was blocked here too.

    A BUILD-TIME check over a whole backtest, deliberately not the live gate:
    the live path's equivalent (`writeback.market_signal_gates`) sees one
    decision, this sees every target the run ever held, and it is the drawdown
    leg that separates them — here it is the WHOLE-WINDOW figure the DoV
    asserts, whereas live the rule is a 36-month rolling ALERT and never blocks
    (ADR-009). Called by the M6-bis validation and by `test_market_signal.py`."""
    violations: list[str] = []
    haven = frozenset({TREND_HAVEN})
    for t, book in sorted(run.targets.items()):
        if not concentration_ok(book, caps, exempt=haven):
            violations.append(f"max_single_asset_pct@{t.date()}")
    if not drawdown_ok(stack_drawdown, caps):
        violations.append("max_drawdown_pct@stack")
    return violations


# -- I/O driver -------------------------------------------------------------


async def run_market_signal(
    db: InvestmentDB,
    *,
    start: date = date(1991, 1, 1),
    end: date = date(2026, 7, 1),
    cadence: str = "monthly",
    cost_bps: float = COST_BPS,
) -> MarketSignalRun:
    """Load the series, run the pure logic, price it on the shared NAV engine.
    Defaults reproduce ADR-007's backtest window and MONTHLY cadence."""
    inputs = await replay.load_inputs(db)
    calendar = replay._book_calendar(inputs)
    rf = await ratios.load_rf_daily(db)

    prices = {t: await ratios.load_price(db, t) for t in STACK_TICKERS}
    prices = {t: p for t, p in prices.items() if not p.empty}
    missing = set(STACK_TICKERS) - set(prices)
    if missing:
        # The exact failure the correction note warns about — refuse to run a
        # stack silently missing a sleeve rather than hold it flat at 0%.
        raise ValueError(f"market-signal stack missing price series for {sorted(missing)}")

    # Keep the RAW series alongside the calendar-aligned one: the ffill that
    # carries a stale print forward is right for the decision (it is what was
    # knowable) but destroys the publication date, and ADR-003 vintage discipline
    # is only auditable if the live path can say WHEN each input became knowable.
    spread_raw = await ratios.load_price(db, CREDIT_SPREAD)
    slope_raw = await ratios.load_price(db, YIELD_SLOPE)
    absent = [t for t, s in ((CREDIT_SPREAD, spread_raw), (YIELD_SLOPE, slope_raw)) if s.empty]
    if absent:
        # The same refusal as the missing sleeve above, for a WORSE failure mode.
        # An absent signal series reindexes to all-NaN, `classify_regime` reads
        # that as warm-up, and the stack silently holds `credit-spread-wide` —
        # the 90%-equity book — on no signal at all. It is not a warm-up: the
        # decision would be uninformed rather than early, and nothing downstream
        # could tell the two apart (`knowable_at` is None in both cases).
        # `seed._missing_stack_series` pre-checks the same two series to SKIP
        # rather than raise (the incremental-seed contract); the LIVE cycle has
        # no such pre-check, and this is what stops it deciding blind.
        raise ValueError(f"market-signal stack missing signal series for {absent}")
    spread = spread_raw.reindex(calendar).ffill()
    slope = slope_raw.reindex(calendar).ffill()
    spread_median = spread.rolling(MEDIAN_WINDOW_DAYS, min_periods=MEDIAN_MIN_DAYS).median()
    slope_median = slope.rolling(MEDIAN_WINDOW_DAYS, min_periods=MEDIAN_MIN_DAYS).median()
    moving_averages = {
        ticker: prices[ticker].rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()
        for ticker in TREND_SLEEVES
    }

    dates = replay.decision_dates(calendar, start, end, cadence)
    decisions = walk_decisions(
        dates, spread, slope, spread_median, slope_median, moving_averages, prices
    )
    targets = {d.date: d.target for d in decisions if d.changed}
    nav, turnover = shadow_book_nav(targets, prices, rf, cost_bps, calendar)
    return MarketSignalRun(
        nav=nav,
        targets=targets,
        turnover=turnover,
        decisions=decisions,
        raw_series={CREDIT_SPREAD: spread_raw, YIELD_SLOPE: slope_raw, **prices},
    )


async def persist_stack_nav(
    db: InvestmentDB, run: MarketSignalRun, window: int
) -> ratios.NavBackfillResult:
    """Write the stack's daily NAV to `portfolio_nav` under STACK_PORTFOLIO_ID.

    A PAPER SERIES, and every reader of it must know so. `shadow_book_nav`
    prices the DECISION WALK: it assumes each monthly target was executed at the
    close of its anchor date, with no slippage, no partial fill and no delay
    between the digest landing and the owner placing the order. V1 executes
    nothing (ADR-006), so no realized series exists to compare it to; this is
    what the STRATEGY would have done, and it is legitimate to rank it against
    portfolios measured the same way — but it is not a statement about the
    owner's account, and the digest and the drawdown alert both say so. Closing
    that gap is forward paper-mode (docs/V1_STRATEGY.md Step 6). One consequence
    worth naming: the walk includes the current month's target even if
    `market_signal_gates` then refused it, so a blocked decision leaves the NAV
    a month ahead of the position. Reachable only through a code or config
    change (ADR-009), which is a bug being surfaced, not a market event.

    The series is `run.nav` — the one `shadow_book_nav` already produced, which
    follows the book through every switch AND every overlay redirect. Persisting
    it is what makes the stack measurable at all: `ratios.value_portfolios`, the
    UC7 ranking and the digest all read `portfolio_nav`, so once this row exists
    the stack's 36M rolling drawdown, Sortino and Calmar arrive through exactly
    the same formulas as every portfolio it is compared against.

    Rebuilt in full on each run rather than appended: the series is DERIVED from
    market data and the pure walk, so recomputing is both cheap and the only way
    a late-arriving price vintage can correct history it should have been in
    (`append_ts_batch` is INSERT OR REPLACE, so this is idempotent).

    The Portfolio vertex's `allocation` is deliberately NOT touched here. That
    column records what the stack HOLDS, which changes only when a decision is
    committed — so it is written inside `writeback.dispose_market_signal`'s
    transaction, after its EventLog append (CLAUDE.md "EventLog"). Writing it
    here would move held state on a week that decided nothing."""
    return await ratios.persist_nav(db, STACK_PORTFOLIO_ID, run.nav.dropna(), window)


async def stack_metrics(db: InvestmentDB, run: MarketSignalRun) -> NavMetrics:
    """Daily NAV metrics of the run (CAGR, Sortino, max drawdown) — the numbers
    the DoV checks against the pinned pair **11.14% / -23.8%** (see the module's
    ANTI-DRIFT note). 11.26% was the pre-ADR-010 figure and 9.85% the
    pre-hysteresis one; both are history, and naming a superseded target here is
    how a drift check comes to certify the wrong number."""
    rf = await ratios.load_rf_daily(db)
    return nav_metrics(run.nav.dropna(), rf)
