"""Momentum + shrunk minimum-variance monthly rotation ("AAAF-R") — a
permanent BENCHMARK against the MS-stack, never a challenger
(db/seed_data.py "aaaf-r-USD", `BENCHMARK_PORTFOLIOS`).

THE RECIPE this refines is published at
info.recipeinvesting.com/recipe/t.aaaf.html; the owner's refinement (2026-08-18)
fixes two implementation gaps in it — a 20-session RAW covariance becomes 63
sessions SHRUNK via Ledoit-Wolf, and an unconstrained min-variance solve
becomes bounded 10-40% per asset — without adding a third mechanism (no
credit spread, no yield curve, no trend overlay: "un challenger autonome et
lisible").

THE RULE, monthly at the last close of each month:
    1. universe: SPY, IWM, QQQ, IYR, TLT, EEM, EFA, GLD, DBC
    2. 180-session total-return momentum per ticker
    3. top 5 by momentum — no trend filter, no cash pass
    4. 63-session daily-return covariance of the top 5
    5. Ledoit-Wolf shrinkage of that covariance
    6. minimum-variance weights on the shrunk covariance, bounds 10-40%,
       sum to 100%
    7. signal computed on the month's last close, EXECUTED the next session
    8. rebalanced monthly, ADR-010's 23bps charged per order
    9. equal-20%-each on insufficient data or optimizer failure — no old
       weight is ever silently carried forward

WHY THIS IS A BENCHMARK, NOT A CHALLENGER (ADR-012 already forbids the Worker
from allocating, so this only concerns the mechanical proposal paths). The
owner's own adoption test is written into the source note: AAAF-R must beat
BOTH the MS-stack AND a plain top-5-equal-weight control before it earns
consideration. That control arm — and a reconstruction of the published
recipe it refines — were explicitly scoped OUT of this build (owner,
2026-08-18: "AAAF-R only"). `BENCHMARK_PORTFOLIOS` membership is what makes
the refusal to propose it STRUCTURAL rather than a reminder someone has to
honour by hand.

WHY THE UNIVERSE IS TRADABLE-ONLY. IWM and IYR carry no `HISTORY_PROXIES`
splice (db/seed_data.py) — the owner chose not to research a pre-inception
proxy for either. So this book's live history starts wherever the 9-ticker
universe is first jointly priced (~2000-06, IYR's Yahoo inception), not 1991
like the rest of the system; `HISTORY_START` below is a REQUEST like
`market_signal.HISTORY_START`, not an anchor — the calendar and the
180-session warm-up decide the real first date.

EXECUTION TIMING mirrors `market_signal.StackSeries.decision_prices` exactly:
every decision is computed on prices SHIFTED BY ONE SESSION (yesterday's
close), and dated on the first trading day of the month
(`replay.decision_dates(..., "monthly")`) — "signal at month-end close,
executed the next session" falls out of that shift with no extra logic.

Ledoit-Wolf shrinkage is `sklearn.covariance.LedoitWolf`, not a hand-derived
formula: `scipy` is already a direct dependency and `scikit-learn` was
already resolved transitively (sentence-transformers), so promoting it to a
direct dependency buys a well-tested implementation instead of a hand-rolled
asymptotic-shrinkage estimator — not a risk worth taking from memory on
portfolio math (CLAUDE.md: "boring over clever")."""

import dataclasses
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios, replay

logger = logging.getLogger(__name__)

PORTFOLIO_ID = "aaaf-r-USD"

UNIVERSE: tuple[str, ...] = ("SPY", "IWM", "QQQ", "IYR", "TLT", "EEM", "EFA", "GLD", "DBC")
TOP_N = 5
MOMENTUM_LOOKBACK_SESSIONS = 180
COV_LOOKBACK_SESSIONS = 63
WEIGHT_MIN = 0.10
WEIGHT_MAX = 0.40
COST_BPS = ratios.TRADING_COST_BPS

# A REQUEST, not an anchor — see module docstring "WHY THE UNIVERSE IS
# TRADABLE-ONLY". Kept at the same literal `market_signal.HISTORY_START` uses
# so both modules say "from the beginning of what exists" the same way.
HISTORY_START = date(1991, 1, 1)

# The pinned 36M window (DATA_MODELS "Calculation conventions"), matching
# `market_signal_cycle.ROLLING_WINDOW_DAYS`. Defaulted rather than read from
# `system_thresholds` so a caller with no threshold loaded needs no extra
# query; callers that have it loaded may still pass their own.
ROLLING_WINDOW_DAYS = 756


def momentum_scores(
    prices: Mapping[str, pd.Series], t: pd.Timestamp, lookback: int = MOMENTUM_LOOKBACK_SESSIONS
) -> dict[str, float]:
    """`TR_i(t)/TR_i(t-lookback) - 1` per ticker (recipe step 2), computed only
    where `lookback` prior sessions exist AS OF `t` — a ticker with fewer is
    skipped rather than scored on a shorter window, so every score in a given
    month is comparable to every other."""
    scores: dict[str, float] = {}
    for ticker, series in prices.items():
        window = series.loc[:t].dropna()
        if len(window) <= lookback:
            continue
        p_t = window.iloc[-1]
        p_lookback = window.iloc[-(lookback + 1)]
        if p_lookback == 0 or not np.isfinite(p_t) or not np.isfinite(p_lookback):
            continue
        scores[ticker] = p_t / p_lookback - 1.0
    return scores


def select_top(scores: Mapping[str, float], n: int = TOP_N) -> list[str]:
    """The `n` highest-momentum tickers (recipe step 3) — no trend filter, no
    cash pass, exactly the recipe's own rule."""
    return [ticker for ticker, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:n]]


def min_variance_weights(returns: pd.DataFrame) -> dict[str, float]:
    """Bounded minimum-variance weights (recipe steps 4-6) on `returns`'
    Ledoit-Wolf-shrunk covariance — columns are tickers, rows the trailing
    `COV_LOOKBACK_SESSIONS` daily returns.

    Falls back to equal weight (recipe step 9) on ANY optimizer failure or
    non-finite result. In practice this is the ONLY branch of step 9 that can
    fire here: momentum needs 180 sessions and the covariance needs only 63,
    so by the time a month has a top-5 at all it always has enough return
    history to shrink — `build_targets` handles the data-insufficiency half
    of step 9 by emitting no target at all during warm-up.

    THE OBJECTIVE IS SELF-NORMALIZED to `w0'Σw0 == 1` at the equal-weight
    start. Found live (not in the recipe, a real bug caught verifying this
    module end-to-end): daily-return covariance entries sit around 1e-4 to
    1e-8, and SLSQP's default finite-difference gradient step is not small
    enough to see a slope that shallow — it read numerical noise as "already
    optimal" and returned the STARTING equal weight as a `success=True`
    solve, on every input tried, silently never reaching the equal-weight
    fallback's warning either. Confirmed with scipy directly: the identical
    unscaled objective froze at `x0` (`fun(x0) == fun(result.x)` exactly)
    across a covariance with clearly different per-asset variances; scaling
    alone (no analytic gradient needed) let it move to the correct low-vol
    tilt. The scale is DERIVED from `returns`, not a fixed constant, so it
    self-calibrates whatever the input's magnitude turns out to be — a
    hardcoded multiplier tuned to one covariance scale would not generalize
    to a calmer or a more volatile month."""
    tickers = list(returns.columns)
    n = len(tickers)
    equal = dict.fromkeys(tickers, 1.0 / n)
    x0 = np.full(n, 1.0 / n)
    try:
        cov = LedoitWolf().fit(returns.to_numpy()).covariance_
        scale0 = float(x0 @ cov @ x0)
        norm = 1.0 / scale0 if scale0 > 0 else 1.0
        result = minimize(
            lambda w: float(w @ cov @ w) * norm,
            x0=x0,
            method="SLSQP",
            bounds=[(WEIGHT_MIN, WEIGHT_MAX)] * n,
            constraints=[{"type": "eq", "fun": lambda w: float(w.sum() - 1.0)}],
        )
    except Exception:
        logger.warning("aaaf-r: min-variance optimizer raised, falling back to equal weight")
        return equal
    if not result.success or not np.all(np.isfinite(result.x)):
        logger.warning(
            "aaaf-r: min-variance optimizer failed (%s), falling back to equal weight",
            result.message,
        )
        return equal
    return dict(zip(tickers, (float(w) for w in result.x), strict=True))


def build_targets(
    dates: Sequence[pd.Timestamp],
    decision_prices: Mapping[str, pd.Series],
) -> dict[pd.Timestamp, dict[str, float]]:
    """The change-point map `replay.shadow_book_nav` consumes — percent
    weights summing to 100, one entry per `dates` where the universe has
    warmed up. Computed ENTIRELY on `decision_prices` (yesterday's close —
    module docstring "EXECUTION TIMING"), so nothing here reads a price the
    order could not yet have used.

    Dates before the 180-session warm-up emit NOTHING, not even an
    equal-weight placeholder — mirroring `market_signal.HISTORY_START`'s "ask
    for the beginning, let the data decide": the walk's first EMITTED target
    is what seeds the book, `shadow_book_nav`'s own contract.

    `Aucun ancien poids silencieusement conservé` (recipe step 9, "no old
    weight is silently kept") is automatic here: every kept date fully
    recomputes momentum and the optimizer from scratch — nothing here reads a
    previous target."""
    targets: dict[pd.Timestamp, dict[str, float]] = {}
    for t in dates:
        scores = momentum_scores(decision_prices, t)
        if len(scores) < TOP_N:
            continue  # still warming up — not every ticker has 180 sessions yet
        top = select_top(scores)
        returns = (
            pd.DataFrame({ticker: decision_prices[ticker] for ticker in top})
            .loc[:t]
            .pct_change()
            .dropna()
            .iloc[-COV_LOOKBACK_SESSIONS:]
        )
        weights = min_variance_weights(returns)
        targets[t] = {ticker: weight * 100.0 for ticker, weight in weights.items()}
    return targets


@dataclasses.dataclass(frozen=True)
class AaafRRun:
    nav: pd.Series
    targets: dict[pd.Timestamp, dict[str, float]]
    turnover: float


async def load_universe_prices(db: InvestmentDB) -> dict[str, pd.Series]:
    prices = {t: await ratios.load_price(db, t) for t in UNIVERSE}
    return {t: p for t, p in prices.items() if not p.empty}


async def run_aaaf_r(
    db: InvestmentDB,
    *,
    start: date = HISTORY_START,
    end: date,
    cost_bps: float = COST_BPS,
) -> AaafRRun:
    """Load the universe, walk the monthly rule, price it on the shared NAV
    engine (`replay.shadow_book_nav` — same engine, same cost convention as
    the MS-stack and its control arm, so the comparison is fair).

    Raises on a missing UNIVERSE ticker — the live cycle (`run_aaaf_r_cycle`)
    pre-checks this and skips gracefully instead; this function is also the
    seed-time producer, which is allowed to raise on a genuinely broken
    prerequisite."""
    prices = await load_universe_prices(db)
    missing = set(UNIVERSE) - set(prices)
    if missing:
        raise ValueError(f"aaaf-r missing price series for {sorted(missing)}")
    calendar = replay.priced_calendar(prices, UNIVERSE)
    rf = await ratios.load_rf_daily(db)
    decision_prices = {t: p.shift(1) for t, p in prices.items()}
    dates = replay.decision_dates(calendar, start, end, "monthly")
    targets = build_targets(dates, decision_prices)
    nav, turnover = replay.shadow_book_nav(targets, prices, rf, cost_bps, calendar)
    return AaafRRun(nav=nav, targets=targets, turnover=turnover)


async def persist_aaaf_r_nav(
    db: InvestmentDB, run: AaafRRun, window: int = ROLLING_WINDOW_DAYS
) -> ratios.NavBackfillResult:
    """Write the daily NAV to `portfolio_nav` (`ratios.persist_nav` — same
    formulas every ranked portfolio uses) and keep `portfolio.allocation`
    live.

    UNCONDITIONAL, unlike `dispose_market_signal`'s write to
    `ms-stack.allocation`: that column protects a HELD vs TARGET distinction
    born from V1 executing nothing (ADR-006) — AAAF-R has no gate and no
    proposal, it simply rebalances every month, so there is no "held" state
    to protect and writing the walk's last target every run is simply
    correct. Leaving it stale would repeat the exact defect CLAUDE.md names
    ("WHEN A SECOND ONE ARRIVES...": a column whose value quietly stops
    matching reality)."""
    result = await ratios.persist_nav(db, PORTFOLIO_ID, run.nav.dropna(), window)
    if run.targets:
        last_target = run.targets[max(run.targets)]
        await db.command(
            "UPDATE portfolio SET allocation = :alloc, updated_at = :now WHERE id = :id",
            alloc=json.dumps(last_target),
            now=datetime.now(UTC).isoformat(),
            id=PORTFOLIO_ID,
        )
    return result


async def missing_universe_series(db: InvestmentDB) -> list[str]:
    """Which of `UNIVERSE` have no MarketData yet. Empty = buildable. Same
    shape as `seed._missing_series`, not imported from there: that helper is
    seed.py-private and parameterised for the stack's own prerequisites."""
    placeholders = ",".join(f":t{n}" for n in range(len(UNIVERSE)))
    rows = await db.query(
        f"SELECT DISTINCT ticker FROM market_data WHERE ticker IN ({placeholders})",
        **{f"t{n}": t for n, t in enumerate(UNIVERSE)},
    )
    present = {str(r["ticker"]) for r in rows}
    return sorted(set(UNIVERSE) - present)


@dataclasses.dataclass(frozen=True)
class AaafRCycleResult:
    """`result` is None exactly when `skipped_reason` is set."""

    result: ratios.NavBackfillResult | None
    skipped_reason: str | None = None


async def run_aaaf_r_cycle(
    db: InvestmentDB,
    *,
    today: date | None = None,
    cost_bps: float = COST_BPS,
    window: int = ROLLING_WINDOW_DAYS,
) -> AaafRCycleResult:
    """The weekly entry point (`weekly.py`, `mechanical/as_of_cycle.py`).
    Refreshes the FULL walk and NAV on every run, like `ms-trend-baseline` —
    AAAF-R has no monthly-decision journal to check for idempotency, it
    simply always recomputes from scratch (cheap: 9 tickers, one optimizer
    solve per historical month).

    A MISSING SERIES WARNS AND SKIPS, NEVER RAISES: a data gap in a
    benchmark (e.g. IWM/IYR not yet fetched on a freshly-upgraded database)
    must not abort the owner's real weekly chain — the same contract
    `seed._seed_portfolio_nav`'s trend-baseline block already holds for the
    MS-stack's control arm."""
    today = today or date.today()
    missing = await missing_universe_series(db)
    if missing:
        logger.warning("aaaf-r cycle skipped: no series for %s", missing)
        return AaafRCycleResult(None, f"missing series: {missing}")
    run = await run_aaaf_r(db, end=today, cost_bps=cost_bps)
    result = await persist_aaaf_r_nav(db, run, window)
    logger.info("aaaf-r nav refreshed through %s (%d rows)", today, result.rows_written)
    return AaafRCycleResult(result)
