"""NAV engine (docs/TASKS.md Phase 5bis `ratios.py`; pinned formulas in
docs/DATA_MODELS.md "Calculation conventions") — synthesizes PortfolioNAV per
the pinned conventions and values Portfolios (UC6).

Split the same way as `market/regime.py`: a PURE core (no I/O, directly
unit-testable against hand-computed golden numbers — docs/MILESTONES.md M4
"golden numbers vs an external source") and a thin async DB layer.
`backfill_nav` is the catch-up/UC0-step-12 WRITER (the only function that
appends to `portfolio_nav`); `value_portfolios` is the UC6 READER — it never
appends TS rows (docs/USE_CASES.md UC6: "PortfolioNAV TS is written by the
Monday 08:00 catch-up job only — UC6 reads it, it does not append").
"""

import dataclasses
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import numpy as np
import pandas as pd

from investment.db.sqlite import InvestmentDB

ALL_WEATHER_ID = "all-weather-USD"
CASH_TICKER = "cash"
RF_TICKER = "^IRX"
TRADING_DAYS_PER_YEAR = 252
# 252/52 rounded — converts a threshold expressed in WEEKS (e.g.
# proposal_outcome_weeks) into the trading-day windows the rolling_* helpers
# take.
TRADING_DAYS_PER_WEEK = 5
# A plain float (not np.sqrt(TRADING_DAYS_PER_YEAR)): multiplying a Series by
# a numpy scalar defeats pandas-stubs' overload resolution and mypy --strict
# reports "Returning Any" on every rolling_* function below.
_ANNUALIZATION = math.sqrt(TRADING_DAYS_PER_YEAR)

# docs/DATA_MODELS.md "Calculation conventions" return_3m/6m/1y/3y/5y windows,
# calendar days.
RETURN_WINDOWS_DAYS: dict[str, int] = {
    "return_3m": 91,
    "return_6m": 182,
    "return_1y": 365,
    "return_3y": 1095,
    "return_5y": 1826,
}

# -- pure core -------------------------------------------------------------


def rf_daily(irx_level: pd.Series) -> pd.Series:
    """`rf_daily = (1 + IRX_level/100)^(1/252) - 1` (latest available ^IRX)."""
    return (1.0 + irx_level / 100.0) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0


def _normalize_weights(allocation: Mapping[str, float]) -> dict[str, float]:
    """`allocation` MAPs are percent weights summing to 100 (DATA_MODELS.md
    'Units convention'); ALL_WEATHER_BENCHMARK is already fractions summing
    to 1. Normalizing by the sum (not a hardcoded /100) makes `synthesize_nav`
    agnostic to which convention the caller used."""
    total = sum(allocation.values())
    return {ticker: weight / total for ticker, weight in allocation.items()}


def synthesize_nav(
    weights: Mapping[str, float], prices: Mapping[str, pd.Series], rf: pd.Series
) -> pd.Series:
    """docs/DATA_MODELS.md 'Calculation conventions' NAV synthesis: constant
    target weights, rebalanced monthly on the first trading day of each
    month, cash sleeve accrues daily at `rf_daily`, `NAV(t0)=100`. `weights`
    must be normalized fractions (see `_normalize_weights`); `prices` covers
    every non-'cash' key of `weights`. Returns a NAV series indexed from the
    first date ALL non-cash constituents have a price (docs/TASKS.md Task
    1ter.7 item 4) — empty if that never happens.

    Rebalance convention: on the first trading day of a new month, sleeves
    are reset to target weights of the PREVIOUS day's total BEFORE that
    day's own return is applied — i.e. the portfolio enters the month
    already rebalanced (Portfolio Visualizer's convention); this is a
    judgment call where the spec is silent on rebalance-day sequencing
    (CLAUDE.md 'state assumptions explicitly')."""
    non_cash = [t for t in weights if t != CASH_TICKER]
    if non_cash:
        price_df = pd.concat({t: prices[t] for t in non_cash}, axis=1, sort=False).sort_index()
        price_df = price_df.dropna(how="any")
    else:
        price_df = pd.DataFrame(index=rf.index[:0])
    if price_df.empty:
        return pd.Series(dtype=float)

    index = price_df.index
    returns = price_df.pct_change()
    rf_aligned = rf.reindex(index).ffill()
    cash_weight = weights.get(CASH_TICKER, 0.0)

    nav = pd.Series(0.0, index=index)
    nav.iloc[0] = 100.0
    sleeve = {t: weights[t] * 100.0 for t in non_cash}
    cash_value = cash_weight * 100.0
    prev_period = index[0].to_period("M")

    for i in range(1, len(index)):
        period = index[i].to_period("M")
        if period != prev_period:
            total_prev = sum(sleeve.values()) + cash_value
            sleeve = {t: weights[t] * total_prev for t in non_cash}
            cash_value = cash_weight * total_prev
            prev_period = period
        for t in non_cash:
            r = returns[t].iloc[i]
            if pd.notna(r):
                sleeve[t] *= 1.0 + r
        cash_value *= 1.0 + rf_aligned.iloc[i]
        nav.iloc[i] = sum(sleeve.values()) + cash_value

    return nav


def daily_returns(nav: pd.Series) -> pd.Series:
    """`NAV(t)/NAV(t-1) - 1`."""
    return nav.pct_change()


def rolling_sharpe(returns: pd.Series, rf: pd.Series, window: int) -> pd.Series:
    """`mean(r - rf_daily) / std(r - rf_daily, ddof=1) * sqrt(252)`.
    `min_periods=2` (not `window`): "if history < 756d, use all available
    history" (DATA_MODELS.md) — pandas' own growing-then-fixed rolling
    window already implements exactly that."""
    excess = returns - rf.reindex(returns.index).ffill()
    mean = excess.rolling(window, min_periods=2).mean()
    std = excess.rolling(window, min_periods=2).std(ddof=1)
    return mean / std * _ANNUALIZATION


def rolling_sortino(returns: pd.Series, rf: pd.Series, window: int) -> pd.Series:
    """`mean(r - rf_daily) / downside_dev * sqrt(252)`,
    `downside_dev = sqrt(mean(min(0, r - rf_daily)^2))` (MAR = rf)."""
    excess = returns - rf.reindex(returns.index).ffill()
    downside_sq = excess.clip(upper=0.0) ** 2
    # `** 0.5`, not `np.sqrt(...)`: a numpy ufunc on a Series defeats
    # pandas-stubs' overload resolution the same way as the annualization
    # constant above (see `_ANNUALIZATION`).
    downside_dev = downside_sq.rolling(window, min_periods=2).mean() ** 0.5
    mean = excess.rolling(window, min_periods=2).mean()
    return mean / downside_dev * _ANNUALIZATION


def rolling_volatility(returns: pd.Series, window: int) -> pd.Series:
    """`std(r, ddof=1) * sqrt(252)`."""
    return returns.rolling(window, min_periods=2).std(ddof=1) * _ANNUALIZATION


def rolling_max_drawdown(nav: pd.Series, window: int) -> pd.Series:
    """`min(NAV/cummax(NAV) - 1)` WITHIN the trailing window."""

    def _mdd(x: np.ndarray) -> float:
        cummax = np.maximum.accumulate(x)
        return float((x / cummax - 1.0).min())

    return nav.rolling(window, min_periods=1).apply(_mdd, raw=True)


def rolling_calmar(nav: pd.Series, max_drawdown: pd.Series, window: int) -> pd.Series:
    """`((NAV_end/NAV_start)^(252/window_days) - 1) / |max_drawdown|`,
    `window_days` = the ACTUAL observation count in the trailing window (may
    be < `window` early in the series)."""

    def _annualized_return(x: np.ndarray) -> float:
        n = len(x)
        if n < 2 or x[0] == 0:
            return float("nan")
        return float((x[-1] / x[0]) ** (TRADING_DAYS_PER_YEAR / n) - 1.0)

    annualized = nav.rolling(window, min_periods=2).apply(_annualized_return, raw=True)
    return annualized / max_drawdown.abs()


def rolling_total_return(nav: pd.Series, window: int) -> pd.Series:
    """Simple cumulative return over the trailing window — used for
    `vs_benchmark` (TASKS.md: "portfolio total_return - ALL_WEATHER_BENCHMARK
    total_return over the same window")."""

    def _total(x: np.ndarray) -> float:
        return float(x[-1] / x[0] - 1.0) if x[0] != 0 else float("nan")

    return nav.rolling(window, min_periods=2).apply(_total, raw=True)


def cumulative_return(nav: pd.Series, as_of: pd.Timestamp, calendar_days: int) -> float | None:
    """`NAV(t)/NAV(t-Nd) - 1` on a calendar window, nearest previous trading
    day (docs/DATA_MODELS.md). `None` if no observation exists that far
    back — "missing data" here means insufficient history, not a gap to
    forward-fill."""
    target = as_of - pd.Timedelta(days=calendar_days)
    eligible = nav.index[nav.index <= target]
    if eligible.empty:
        return None
    start_nav = nav.loc[eligible[-1]]
    end_nav = nav.loc[as_of]
    if start_nav == 0:
        return None
    return float(end_nav / start_nav - 1.0)


def flt(value: Any) -> float | None:
    """NaN-safe float coercion — shared by every mechanical module that reads
    a `.iloc[-1]` off a pandas Series (which is `np.float64`, not `float`,
    and may be NaN rather than a clean missing marker)."""
    if value is None:
        return None
    f = float(value)
    return None if np.isnan(f) else f


# -- async DB layer (writer path — agent-only, ADR-004/ADR-005) ------------


@dataclasses.dataclass(frozen=True)
class NavBackfillResult:
    portfolio_id: str
    rows_written: int
    start_date: str | None


@dataclasses.dataclass(frozen=True)
class PortfolioValuation:
    portfolio_id: str
    sharpe_rolling: float | None
    sortino_rolling: float | None
    calmar_rolling: float | None
    max_drawdown: float | None
    volatility: float | None
    return_3m: float | None
    return_6m: float | None
    return_1y: float | None
    return_3y: float | None
    return_5y: float | None


async def _price_series(db: InvestmentDB, ticker: str) -> pd.Series:
    rows = await db.query(
        "SELECT ts, level FROM market_data WHERE ticker = :t AND level IS NOT NULL ORDER BY ts",
        t=ticker,
    )
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([r["ts"] for r in rows])
    return pd.Series([r["level"] for r in rows], index=idx, dtype=float)


async def _rf_daily_series(db: InvestmentDB) -> pd.Series:
    return rf_daily(await _price_series(db, RF_TICKER))


async def load_price(db: InvestmentDB, ticker: str) -> pd.Series:
    """Public wrapper around `_price_series` — the raw `level` column, for
    other mechanical modules (backtests.py benchmark-class construction)."""
    return await _price_series(db, ticker)


async def load_rf_daily(db: InvestmentDB) -> pd.Series:
    """Public wrapper around `_rf_daily_series`."""
    return await _rf_daily_series(db)


async def load_nav(db: InvestmentDB, portfolio_id: str) -> pd.Series:
    """Public wrapper around `_load_nav_column` — the already-backfilled
    `portfolio_nav.nav` series, for other mechanical modules."""
    return await _load_nav_column(db, portfolio_id, "nav")


async def _load_nav_column(db: InvestmentDB, portfolio_id: str, column: str) -> pd.Series:
    rows = await db.query(
        f"SELECT ts, {column} AS v FROM portfolio_nav WHERE portfolio_id = :pid "
        "AND v IS NOT NULL ORDER BY ts",
        pid=portfolio_id,
    )
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([r["ts"] for r in rows])
    return pd.Series([r["v"] for r in rows], index=idx, dtype=float)


def _nav_rows(portfolio_id: str, currency: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    df = frame.astype(object).where(pd.notna(frame), None)
    # pandas-stubs types to_dict("records") keys as Hashable (the general
    # case); every column here is a plain string.
    records = cast("list[dict[str, Any]]", df.to_dict("records"))
    return [
        {"portfolio_id": portfolio_id, "currency": currency, "ts": ts.date().isoformat(), **record}
        for ts, record in zip(df.index, records, strict=True)
    ]


async def _vs_benchmark(
    db: InvestmentDB, portfolio_id: str, nav: pd.Series, window: int
) -> pd.Series:
    if portfolio_id == ALL_WEATHER_ID:
        return pd.Series(np.nan, index=nav.index)
    benchmark_nav = await _load_nav_column(db, ALL_WEATHER_ID, "nav")
    if benchmark_nav.empty:
        return pd.Series(np.nan, index=nav.index)
    portfolio_total = rolling_total_return(nav, window)
    benchmark_total = rolling_total_return(benchmark_nav.reindex(nav.index).ffill(), window)
    return portfolio_total - benchmark_total


async def backfill_nav(
    db: InvestmentDB, portfolio_id: str, allocation: Mapping[str, float], window: int
) -> NavBackfillResult:
    """UC0 step 12 / Monday 08:00 catch-up writer. Idempotent (`append_ts_batch`
    is INSERT OR REPLACE). The ALL_WEATHER_BENCHMARK series (`portfolio_id=
    ALL_WEATHER_ID`) must be backfilled BEFORE any real portfolio so
    `vs_benchmark` can read it back — enforced by caller ordering
    (`seed._seed_portfolio_nav`), not here."""
    weights = _normalize_weights(allocation)
    non_cash = [t for t in weights if t != CASH_TICKER]
    prices = {t: await _price_series(db, t) for t in non_cash}
    rf = await _rf_daily_series(db)

    nav = synthesize_nav(weights, prices, rf)
    if nav.empty:
        return NavBackfillResult(portfolio_id, 0, None)

    returns = daily_returns(nav)
    max_drawdown = rolling_max_drawdown(nav, window)
    frame = pd.DataFrame(
        {
            "nav": nav,
            "daily_return": returns,
            "sharpe_rolling": rolling_sharpe(returns, rf, window),
            "sortino_rolling": rolling_sortino(returns, rf, window),
            "calmar_rolling": rolling_calmar(nav, max_drawdown, window),
            "drawdown": max_drawdown,
            "vs_benchmark": await _vs_benchmark(db, portfolio_id, nav, window),
        }
    )
    rows = _nav_rows(portfolio_id, "USD", frame)
    await db.append_ts_batch("portfolio_nav", rows)
    return NavBackfillResult(portfolio_id, len(rows), nav.index[0].date().isoformat())


async def value_portfolios(db: InvestmentDB, window: int) -> list[PortfolioValuation]:
    """UC6 — reads the latest PortfolioNAV row per enabled Portfolio (never
    appends), derives `volatility` and cumulative `return_*` from the
    already-persisted daily_return/nav series, and updates each Portfolio
    vertex's indicator fields. Appends ONE ValuationEvent for the batch,
    BEFORE the vertex updates (CLAUDE.md 'EventLog' rule)."""
    portfolio_rows = await db.query("SELECT id FROM portfolio WHERE enabled = 1")

    valuations: list[PortfolioValuation] = []
    for row in portfolio_rows:
        portfolio_id = str(row["id"])
        ts_rows = await db.query(
            "SELECT ts, nav, daily_return, sharpe_rolling, sortino_rolling, calmar_rolling, "
            "drawdown FROM portfolio_nav WHERE portfolio_id = :pid ORDER BY ts",
            pid=portfolio_id,
        )
        if not ts_rows:
            continue
        idx = pd.DatetimeIndex([r["ts"] for r in ts_rows])
        nav = pd.Series([r["nav"] for r in ts_rows], index=idx, dtype=float)
        returns = pd.Series([r["daily_return"] for r in ts_rows], index=idx, dtype=float)
        latest = ts_rows[-1]
        as_of = idx[-1]
        volatility = rolling_volatility(returns, window).iloc[-1]

        valuations.append(
            PortfolioValuation(
                portfolio_id=portfolio_id,
                sharpe_rolling=flt(latest["sharpe_rolling"]),
                sortino_rolling=flt(latest["sortino_rolling"]),
                calmar_rolling=flt(latest["calmar_rolling"]),
                max_drawdown=flt(latest["drawdown"]),
                volatility=flt(volatility),
                **{
                    field: cumulative_return(nav, as_of, days)
                    for field, days in RETURN_WINDOWS_DAYS.items()
                },
            )
        )

    if not valuations:
        return []

    async with db.transaction():
        await db.append_event(
            type="ValuationEvent",
            source_uc="UC6",
            source_id=None,
            payload={"portfolios": [dataclasses.asdict(v) for v in valuations]},
        )
        now = datetime.now(UTC).isoformat()
        for v in valuations:
            await db.command(
                "UPDATE portfolio SET sharpe_rolling = :sharpe, sortino_rolling = :sortino, "
                "calmar_rolling = :calmar, max_drawdown = :mdd, volatility = :vol, "
                "return_3m = :r3m, return_6m = :r6m, return_1y = :r1y, return_3y = :r3y, "
                "return_5y = :r5y, updated_at = :now WHERE id = :id",
                sharpe=v.sharpe_rolling,
                sortino=v.sortino_rolling,
                calmar=v.calmar_rolling,
                mdd=v.max_drawdown,
                vol=v.volatility,
                r3m=v.return_3m,
                r6m=v.return_6m,
                r1y=v.return_1y,
                r3y=v.return_3y,
                r5y=v.return_5y,
                now=now,
                id=v.portfolio_id,
            )

    return valuations
