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
figures ADR-007 was signed on. `run_market_signal` reproduces them; M6-bis's Definition
of Verified is that reproduction.

PURE decision logic (`classify_regime`, `apply_trend_overlay`, `build_targets`)
takes already-loaded series and holds no I/O — the same separation as
`mechanical/gates.py`, so the classifier is unit-testable without a DB and the
eventual live monthly decision path (M8 Writeback) calls the identical function
the replay validates. `run_market_signal` is the thin I/O driver.
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
    "wide-credit": {"SPY": 50.0, "IWN": 40.0, "GLD": 10.0},
    "tight-flat": {"SPY": 50.0, "GLD": 40.0, "IWN": 10.0},
    "tight-steep": {"VCIT": 50.0, "IEF": 40.0, "IWN": 10.0},
}

# The 200d trend overlay: these sleeves are redirected to TREND_HAVEN when their
# own price is below their 200-day moving average. This is the drawdown control
# (-24% with it, -50% without — docs/V1_STRATEGY.md).
TREND_SLEEVES: tuple[str, ...] = ("SPY", "GLD")
TREND_HAVEN = "IEF"

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

COST_BPS = 20.0


@dataclasses.dataclass(frozen=True)
class MarketSignalRun:
    """A backtest/replay of the stack over a window.

    `targets` maps each CHANGE date -> the book that took effect (only dates
    where the allocation actually changed, matching `shadow_book_nav`'s
    time-varying target contract); `turnover` is its summed round-trip turnover.
    """

    nav: pd.Series
    targets: dict[pd.Timestamp, dict[str, float]]
    turnover: float


# -- pure decision logic (no I/O — unit-testable, shared with the live path) --


def classify_regime(
    spread: float, spread_median: float | None, slope: float, slope_median: float | None
) -> str:
    """The market-signal regime (docs/V1_STRATEGY.md "Regime signal"):
    credit spread WIDE vs its 10y median -> `wide-credit` (stress is PRICED, so
    the countercyclical response is to buy risk); else, on the slope: FLAT vs
    its 10y median -> `tight-flat`, STEEP -> `tight-steep`.

    The returned key names the SIGNAL STATE, not a macro regime — see BOOKS.

    A missing median (warm-up, before MEDIAN_MIN_DAYS of history) defaults to
    `wide-credit` — the equity-tilted book — exactly as the backtest did rather
    than stalling; the trend overlay still guards its downside."""
    if spread_median is None or pd.isna(spread_median) or spread > spread_median:
        return "wide-credit"
    if slope_median is None or pd.isna(slope_median) or slope < slope_median:
        return "tight-flat"
    return "tight-steep"


def apply_trend_overlay(book: Mapping[str, float], below_trend: frozenset[str]) -> dict[str, float]:
    """Redirect each TREND_SLEEVES weight to TREND_HAVEN when that sleeve is
    below its 200d MA. Weights merge additively — if a book already holds
    TREND_HAVEN (the slowdown book holds IEF), a redirected sleeve adds to it."""
    adjusted: dict[str, float] = {}
    for ticker, weight in book.items():
        destination = TREND_HAVEN if ticker in TREND_SLEEVES and ticker in below_trend else ticker
        adjusted[destination] = adjusted.get(destination, 0.0) + float(weight)
    return adjusted


def build_targets(
    dates: Sequence[pd.Timestamp],
    spread: pd.Series,
    slope: pd.Series,
    spread_median: pd.Series,
    slope_median: pd.Series,
    moving_averages: Mapping[str, pd.Series],
    prices: Mapping[str, pd.Series],
) -> dict[pd.Timestamp, dict[str, float]]:
    """Walk the decision clock and emit a target ONLY when the book changes —
    the change-point map `shadow_book_nav` consumes (a monthly re-evaluation
    that lands on the same book pays no turnover)."""
    targets: dict[pd.Timestamp, dict[str, float]] = {}
    previous: dict[str, float] | None = None
    for t in dates:
        regime = classify_regime(
            _at(spread, t), _at(spread_median, t), _at(slope, t), _at(slope_median, t)
        )
        below_trend = frozenset(
            ticker
            for ticker in TREND_SLEEVES
            if ticker in prices
            and pd.notna(_at(moving_averages[ticker], t))
            and _at(prices[ticker], t) < _at(moving_averages[ticker], t)
        )
        book = apply_trend_overlay(BOOKS[regime], below_trend)
        if book != previous:
            targets[t] = book
            previous = book
    return targets


def _at(series: pd.Series, t: pd.Timestamp) -> float:
    """Point read that tolerates a decision date off the series index (returns
    NaN), so `classify_regime`'s warm-up default fires instead of a KeyError."""
    value = series.get(t)
    return float("nan") if value is None else float(value)


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
    conviction bet. Uses the SAME `gates.py` predicate the live Writeback (M8)
    will, with the same exemption, so a book that would be blocked live is
    blocked here too."""
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

    spread = (await ratios.load_price(db, CREDIT_SPREAD)).reindex(calendar).ffill()
    slope = (await ratios.load_price(db, YIELD_SLOPE)).reindex(calendar).ffill()
    spread_median = spread.rolling(MEDIAN_WINDOW_DAYS, min_periods=MEDIAN_MIN_DAYS).median()
    slope_median = slope.rolling(MEDIAN_WINDOW_DAYS, min_periods=MEDIAN_MIN_DAYS).median()
    moving_averages = {
        ticker: prices[ticker].rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()
        for ticker in TREND_SLEEVES
    }

    dates = replay.decision_dates(calendar, start, end, cadence)
    targets = build_targets(
        dates, spread, slope, spread_median, slope_median, moving_averages, prices
    )
    nav, turnover = shadow_book_nav(targets, prices, rf, cost_bps, calendar)
    return MarketSignalRun(nav=nav, targets=targets, turnover=turnover)


async def stack_metrics(db: InvestmentDB, run: MarketSignalRun) -> NavMetrics:
    """Daily NAV metrics of the run (CAGR, Sortino, max drawdown) — the numbers
    the DoV checks against 9.85% / -24%."""
    rf = await ratios.load_rf_daily(db)
    return nav_metrics(run.nav.dropna(), rf)
