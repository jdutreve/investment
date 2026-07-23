"""Backtests + FAVORS + benchmark_valuation (docs/TASKS.md Phase 5bis
`backtests.py`; docs/USE_CASES.md UC0 steps 10b/11; docs/DATA_MODELS.md
Backtest/FAVORS/benchmark_valuation entities) — "define and value the
benchmarks before valuing invariants" (10b), then synthetic backtests per
(Strategy x RegimeType) over historical Regime instances (11), refreshing
the FAVORS aggregate. `mechanical/invariants.py`'s `mature_invariant()` reads
`benchmark_valuation` (10b) as its cross_class/cross_strategy comparator —
this module must run before it.

Split the same way as `market/regime.py` / `mechanical/ratios.py`: a PURE
core (no I/O) and a thin async DB layer.
"""

import dataclasses
from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd
from ulid import ULID

from investment.db.seed_data import BENCHMARK_CLASSES
from investment.db.sqlite import InvestmentDB
from investment.market import derivatives
from investment.mechanical import ratios

BENCHMARK_KIND_ASSET_CLASS = "asset_class"
BENCHMARK_KIND_STRATEGY = "strategy"
# Per-TICKER rows. The spec's validation gate admits an `asset:<ticker>`
# handle with method cross_class ("cross_class ⇒ asset/class handle" —
# docs/ARCHITECTURE.md), which needs the asset's own metrics on the same
# pre-materialised footing as the class/strategy benchmarks it is compared
# against. A class row cannot stand in: 'gold-commodities' blends GLD with
# DJP/DBC, so an invariant about GOLD would be scored on a different asset.
BENCHMARK_KIND_ASSET = "asset"
REAL_RATE_TICKER = "real_rate"
REAL_YIELD_10Y_TICKER = "real_yield_10y"
M2_YOY_TICKER = "m2_yoy"
M2_ACCEL_TICKER = "m2_accel_12m"
EQUITY_TREND_TICKER = "equity_trend"
GOLD_10Y_DEV_TICKER = "gold_10y_dev"

# The metrics `period_series_frame` computes — i.e. the ONLY values an
# `effect.metric` may name (docs/ARCHITECTURE.md VALIDATION GATE: "`metric` a
# computed indicator"). The confrontation reads the metric as a COLUMN of the
# benchmark frames, so anything outside this set is a KeyError mid-sweep, not
# a demotion — which is exactly what the gate exists to prevent.
BENCHMARK_METRICS = frozenset({"return", "sortino_rolling", "max_drawdown", "volatility"})

# -- pure core ---------------------------------------------------------


def blended_class_nav(prices: dict[str, pd.Series]) -> pd.Series:
    """Equal-weighted index across whichever of a coarse benchmark class's
    constituent tickers have data on a given day — judgment call: DATA_MODELS
    says the asset_class rows are built "from constituent prices" without
    pinning a blending method. Weighting by AVAILABLE constituents (not a
    fixed set, which `ratios.synthesize_nav`'s `dropna(how="any")` requires)
    lets the index reach each ticker's own earliest history instead of being
    gated by the LATEST-starting one — e.g. EEM (2003) would otherwise
    truncate the whole 'equities' class benchmark to 2003, when SPY/VTI (via
    HISTORY_PROXIES) reach back to ~1991 (docs/USE_CASES.md UC0 step 10b
    "tradable history to ~1991"). `NAV(t0)=100` at the first date any
    constituent has a computable return, matching `ratios.synthesize_nav`'s
    convention."""
    if not prices:
        return pd.Series(dtype=float)
    returns = pd.concat(
        {t: p.sort_index().pct_change() for t, p in prices.items()}, axis=1, sort=False
    )
    avg_return = returns.mean(axis=1, skipna=True)
    first_valid = avg_return.first_valid_index()
    if first_valid is None:
        return pd.Series(dtype=float)
    avg_return = avg_return.loc[first_valid:].fillna(0.0)
    avg_return.iloc[0] = 0.0
    return 100.0 * (1.0 + avg_return).cumprod()


def cash_class_nav(rf: pd.Series) -> pd.Series:
    """The 'cash' benchmark class carries no fetchable ticker (US_TBILL, no
    viable HISTORY_PROXIES splice — seed_data.py BENCHMARK_CLASSES note); it
    is represented by the same synthetic series the portfolio 'cash' sleeve
    uses: accrual at `rf_daily`, `NAV(t0)=100`."""
    if rf.empty:
        return pd.Series(dtype=float)
    r = rf.fillna(0.0).copy()
    r.iloc[0] = 0.0
    return 100.0 * (1.0 + r).cumprod()


@dataclasses.dataclass(frozen=True)
class PeriodMetrics:
    sharpe_rolling: float | None
    sortino_rolling: float | None
    calmar_rolling: float | None
    max_drawdown: float | None
    total_return: float | None


def period_metrics(nav: pd.Series, rf: pd.Series) -> PeriodMetrics:
    """Whole-slice sharpe/sortino/calmar/max_drawdown/total_return, reusing
    the pinned `ratios.py` rolling_* formulas with `window = len(nav)` so the
    'rolling' window covers exactly the backtest period (docs/DATA_MODELS.md
    Backtest: "may be shorter than 36M — field name kept uniform for query
    symmetry")."""
    window = len(nav)
    if window < 2:
        return PeriodMetrics(None, None, None, None, None)
    returns = ratios.daily_returns(nav)
    max_dd = ratios.rolling_max_drawdown(nav, window)
    return PeriodMetrics(
        sharpe_rolling=ratios.flt(ratios.rolling_sharpe(returns, rf, window).iloc[-1]),
        sortino_rolling=ratios.flt(ratios.rolling_sortino(returns, rf, window).iloc[-1]),
        calmar_rolling=ratios.flt(ratios.rolling_calmar(nav, max_dd, window).iloc[-1]),
        max_drawdown=ratios.flt(max_dd.iloc[-1]),
        total_return=ratios.flt(ratios.rolling_total_return(nav, window).iloc[-1]),
    )


def _mean_or_none(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return float(np.mean(present)) if present else None


def aggregate_metrics(rows: list[PeriodMetrics]) -> PeriodMetrics:
    """FAVORS aggregation across all of a RegimeType's Backtest rows for one
    Strategy — simple mean per field (judgment call: "aggregated across all
    historical instances" does not pin a weighting; equal-weight-per-instance
    is the least surprising default)."""
    return PeriodMetrics(
        sharpe_rolling=_mean_or_none([r.sharpe_rolling for r in rows]),
        sortino_rolling=_mean_or_none([r.sortino_rolling for r in rows]),
        calmar_rolling=_mean_or_none([r.calmar_rolling for r in rows]),
        max_drawdown=_mean_or_none([r.max_drawdown for r in rows]),
        total_return=_mean_or_none([r.total_return for r in rows]),
    )


def period_series_frame(nav: pd.Series, rf: pd.Series, window: int) -> pd.DataFrame:
    """The benchmark_valuation columns (return/sortino_rolling/max_drawdown/
    volatility) as a TRAILING-`window` rolling series, resampled to weekly
    anchors (docs/DATA_MODELS.md benchmark_valuation: "Grows per period ...
    extended weekly").

    `window` is the CONFRONTATION HORIZON (proposal_outcome_weeks, ~60
    trading days), NOT the 756d ranking window. Why trailing, when
    `invariants.py` needs the window FOLLOWING a condition-moment: a row
    dated `d` holds metrics over `[d-horizon, d]`, so it is knowable at `d`
    and the table stays point-in-time (ADR-003) — storing forward-looking
    metrics under `d` would plant look-ahead that the M6 replay reads back
    as-of-t. The confrontation gets its forward window by reading the row at
    `d+horizon` instead (invariants.py `_asof_forward`), which is the same
    number without the leak."""
    returns = ratios.daily_returns(nav)
    max_dd = ratios.rolling_max_drawdown(nav, window)
    frame = pd.DataFrame(
        {
            "return": ratios.rolling_total_return(nav, window),
            "sortino_rolling": ratios.rolling_sortino(returns, rf, window),
            "max_drawdown": max_dd,
            "volatility": ratios.rolling_volatility(returns, window),
        }
    )
    return frame.resample("W-FRI").last().dropna(how="all")


# -- async DB layer (writer path — agent-only, ADR-004/ADR-005) ------------


def _ts_rows(
    ticker: str, asset_class: str, currency: str, deriv: pd.DataFrame
) -> list[dict[str, Any]]:
    df = deriv.astype(object).where(pd.notna(deriv), None)
    records = cast("list[dict[str, Any]]", df.to_dict("records"))
    return [
        {
            "ticker": ticker,
            "asset_class": asset_class,
            "currency": currency,
            "ts": ts.date().isoformat(),
            **record,
        }
        for ts, record in zip(df.index, records, strict=True)
    ]


# The real-rate family of DERIVED_SIGNALS (db/seed_data.py): each is a
# NOMINAL yield minus inflation, differing only in the maturity of the
# nominal leg — `derived id -> nominal ticker`. They are distinct signals,
# not variants: the short real rate sits below 2.5% for 88% of 1991-2026
# while the long real yield does not, so a condition threshold means an
# entirely different thing on each.
_REAL_RATE_SIGNALS: dict[str, str] = {
    REAL_RATE_TICKER: ratios.RF_TICKER,
    REAL_YIELD_10Y_TICKER: "DGS10",
}


async def _materialize_real_rates(db: InvestmentDB, default_lookback_days: int) -> dict[str, int]:
    """The nominal-minus-inflation DERIVED_SIGNALS (docs/TASKS.md), from
    series step 9 already persisted. Inflation is reindexed onto the nominal
    leg's (daily) calendar and forward-filled: CPI prints monthly, so its
    last KNOWN value stands until the next print — point-in-time by
    construction (ADR-003), never interpolated forward."""
    inflation = await ratios.load_price(db, "CPIAUCSL")
    written: dict[str, int] = {}
    for derived_id, nominal_ticker in _REAL_RATE_SIGNALS.items():
        nominal = await ratios.load_price(db, nominal_ticker)
        if nominal.empty or inflation.empty:
            written[derived_id] = 0
            continue
        real = (nominal - inflation.reindex(nominal.index).ffill()).dropna()
        if real.empty:
            written[derived_id] = 0
            continue
        deriv = derivatives.compute_derivatives(real, derived_id, default_lookback_days)
        rows = _ts_rows(derived_id, "MACRO", "USD", deriv)
        await db.append_ts_batch("market_data", rows)
        written[derived_id] = len(rows)

    written.update(await _materialize_broad_money(db, default_lookback_days))
    written[EQUITY_TREND_TICKER] = await _materialize_equity_trend(db, default_lookback_days)
    written[GOLD_10Y_DEV_TICKER] = await _materialize_gold_10y_dev(db, default_lookback_days)
    return written


def _yoy_change(series: pd.Series, days: int = 365) -> pd.Series:
    """`x(t) - x(t - days)` on the series' own (irregular) calendar, via the
    latest observation at or before the lagged date — the same as-of read
    `market/derivatives.py` uses, so a monthly series is not interpolated."""
    # pandas-stubs types `.index` as a generic Index, which has no
    # Timedelta arithmetic; these series are always date-indexed.
    idx = pd.DatetimeIndex(series.index)
    lagged = series.reindex(idx - pd.Timedelta(days=days), method="ffill")
    return pd.Series(series.to_numpy() - lagged.to_numpy(), index=idx).dropna()


async def _materialize_broad_money(db: InvestmentDB, default_lookback_days: int) -> dict[str, int]:
    """`m2_yoy` (broad-money GROWTH) and `m2_accel_12m` (is that growth FASTER
    than a year ago) — db/seed_data.py DERIVED_SIGNALS.

    The 12-month spans are the point: an annual money-growth claim is not
    expressible through `GLOBAL_LIQUIDITY`, whose speed/acceleration are
    7-DAY (measured on the real data, the two readings of "positive and
    accelerating" disagree on 42.7% of days). M2 is the only LIVE broad
    aggregate — every US M3 series is discontinued (M5)."""
    m2 = await ratios.load_price(db, "M2SL")
    if m2.empty:
        return {M2_YOY_TICKER: 0, M2_ACCEL_TICKER: 0}
    # Percent growth, not a level difference: M2 grew ~30x since 1959, so an
    # absolute change is not comparable across the sample.
    idx = pd.DatetimeIndex(m2.index)
    lagged = m2.reindex(idx - pd.Timedelta(days=365), method="ffill")
    yoy = (pd.Series(m2.to_numpy() / lagged.to_numpy() - 1.0, index=idx) * 100.0).dropna()
    written: dict[str, int] = {}
    for derived_id, series in ((M2_YOY_TICKER, yoy), (M2_ACCEL_TICKER, _yoy_change(yoy))):
        if series.empty:
            written[derived_id] = 0
            continue
        deriv = derivatives.compute_derivatives(series, derived_id, default_lookback_days)
        rows = _ts_rows(derived_id, "MACRO", "USD", deriv)
        await db.append_ts_batch("market_data", rows)
        written[derived_id] = len(rows)
    return written


async def _materialize_equity_trend(db: InvestmentDB, default_lookback_days: int) -> int:
    """`equity_trend` = SPY / SMA10-month(SPY) - 1: >0 iff equities sit above
    their 10-month average (Faber; Moskowitz-Ooi-Pedersen time-series
    momentum). SPY carries its HISTORY_PROXIES splice, so this reaches ~1980.

    The SMA is trailing-only (`rolling`), so the signal is knowable on its
    own date — no look-ahead (ADR-003)."""
    spy = await ratios.load_price(db, "SPY")
    if spy.empty:
        return 0
    # 210 trading days ~ 10 months. min_periods=window: a trend filter with a
    # half-formed average is not the claim, and a partial SMA would emit a
    # confident-looking value from a handful of points.
    sma = spy.rolling(210, min_periods=210).mean()
    trend = ((spy / sma - 1.0) * 100.0).dropna()
    if trend.empty:
        return 0
    deriv = derivatives.compute_derivatives(trend, EQUITY_TREND_TICKER, default_lookback_days)
    rows = _ts_rows(EQUITY_TREND_TICKER, "MACRO", "USD", deriv)
    await db.append_ts_batch("market_data", rows)
    return len(rows)


async def _materialize_gold_10y_dev(db: InvestmentDB, default_lookback_days: int) -> int:
    """`gold_10y_dev` = log(GLD / DGS10 / SMA84m(GLD / DGS10)): gold priced in
    units of the 10y nominal yield, as a log deviation from its own 7-year
    trend (db/seed_data.py DERIVED_SIGNALS; inv-gold-ratio-trend-tilt).

    The 84-month SMA is computed on a MONTHLY resample (the owner's literal
    'SMA84', 84 monthly points), then forward-filled to a daily calendar so
    the confrontation's daily condition sweep can read it — point-in-time: the
    last known month-end deviation stands until the next month prints (ADR-003,
    like every other monthly DERIVED_SIGNAL here). `speed` is a 6-month
    lookback (market/derivatives.py override), matching the note's momentum
    leg `D - D[-6 months]`. GLD carries its HISTORY_PROXIES splice and DGS10
    reaches 1991, so the ratio starts ~1991 and the deviation ~1998 (7y of
    SMA)."""
    gld = await ratios.load_price(db, "GLD")
    dgs10 = await ratios.load_price(db, "DGS10")
    if gld.empty or dgs10.empty:
        return 0
    cal = pd.date_range(
        max(gld.index.min(), dgs10.index.min()), max(gld.index.max(), dgs10.index.max()), freq="D"
    )
    ratio = gld.reindex(cal).ffill() / dgs10.reindex(cal).ffill().replace(0.0, np.nan)
    monthly = ratio.resample("ME").last()
    # min_periods=84: a 7-year trend from a half-formed window is not the
    # claim (same discipline as equity_trend's 210-day min_periods).
    sma84 = monthly.rolling(84, min_periods=84).mean()
    log_dev = pd.Series(np.log(monthly / sma84), index=monthly.index)
    deviation = log_dev.reindex(cal).ffill().dropna()
    if deviation.empty:
        return 0
    deriv = derivatives.compute_derivatives(deviation, GOLD_10Y_DEV_TICKER, default_lookback_days)
    rows = _ts_rows(GOLD_10Y_DEV_TICKER, "MACRO", "USD", deriv)
    await db.append_ts_batch("market_data", rows)
    return len(rows)


async def _class_constituents(db: InvestmentDB, fine_classes: list[str]) -> list[str]:
    if not fine_classes:
        return []
    placeholders = ", ".join(f":c{i}" for i in range(len(fine_classes)))
    rows = await db.query(
        f"SELECT ticker FROM allowed_tickers WHERE asset_class IN ({placeholders}) AND active = 1",
        **{f"c{i}": c for i, c in enumerate(fine_classes)},
    )
    return [str(r["ticker"]) for r in rows]


async def _class_nav(db: InvestmentDB, coarse_class: str, rf: pd.Series) -> pd.Series:
    if coarse_class == "cash":
        return cash_class_nav(rf)
    tickers = await _class_constituents(db, BENCHMARK_CLASSES[coarse_class])
    prices = {t: p for t in tickers if not (p := await ratios.load_price(db, t)).empty}
    return blended_class_nav(prices)


async def investable_tickers(db: InvestmentDB) -> dict[str, str]:
    """`ticker -> coarse BENCHMARK_CLASSES key` for every asset that is a
    constituent of a benchmark class — i.e. every ticker an `asset:<ticker>`
    handle may legally name. Macro/FX/VIX/^IRX are excluded by construction
    (they belong to no benchmark class: they are signals, not sleeves)."""
    fine_to_coarse = {fine: coarse for coarse, fines in BENCHMARK_CLASSES.items() for fine in fines}
    rows = await db.query(
        "SELECT ticker, asset_class FROM allowed_tickers WHERE active = 1 ORDER BY ticker"
    )
    result = {
        str(r["ticker"]): fine_to_coarse[str(r["asset_class"])]
        for r in rows
        if str(r["asset_class"]) in fine_to_coarse
    }
    # The synthetic 'cash' asset has no allowed_tickers row (it accrues at
    # rf_daily rather than being fetched — db/seed_data.py BENCHMARK_CLASSES).
    result[ratios.CASH_TICKER] = "cash"
    return result


async def _asset_nav(db: InvestmentDB, ticker: str, rf: pd.Series) -> pd.Series:
    if ticker == ratios.CASH_TICKER:
        return cash_class_nav(rf)
    price = await ratios.load_price(db, ticker)
    # A single-constituent blend IS the asset's own total-return index,
    # rebased to 100 — same construction as its class, so asset and class
    # rows stay directly comparable.
    return blended_class_nav({ticker: price}) if not price.empty else pd.Series(dtype=float)


async def _primary_portfolio_id(db: InvestmentDB, strategy_id: str) -> str | None:
    rows = await db.query(
        "SELECT portfolio_id FROM holds WHERE strategy_id = :sid AND is_primary = 1 LIMIT 1",
        sid=strategy_id,
    )
    return str(rows[0]["portfolio_id"]) if rows else None


async def _write_benchmark_series(
    db: InvestmentDB, benchmark_kind: str, benchmark_id: str, frame: pd.DataFrame
) -> int:
    """Idempotent by the (benchmark_kind, benchmark_id, date) UNIQUE index
    (schema.py) — `INSERT OR REPLACE` deletes-then-reinserts a colliding row
    under a fresh synthetic id, which is safe: `id` has no incoming FK."""
    if frame.empty:
        return 0
    clean = frame.astype(object).where(pd.notna(frame), None)
    records = cast("list[dict[str, Any]]", clean.to_dict("records"))
    async with db.transaction():
        for ts, record in zip(frame.index, records, strict=True):
            await db.command(
                "INSERT OR REPLACE INTO benchmark_valuation "
                "(id, benchmark_kind, benchmark_id, date, return, sortino_rolling, "
                " max_drawdown, volatility) "
                "VALUES (:id, :kind, :bid, :date, :ret, :sortino, :mdd, :vol)",
                id=str(ULID()),
                kind=benchmark_kind,
                bid=benchmark_id,
                date=ts.date().isoformat(),
                ret=record["return"],
                sortino=record["sortino_rolling"],
                mdd=record["max_drawdown"],
                vol=record["volatility"],
            )
    return len(records)


@dataclasses.dataclass(frozen=True)
class BenchmarkValuationResult:
    derived_signal_rows: dict[str, int]
    asset_class_rows: dict[str, int]
    strategy_rows: dict[str, int]
    asset_rows: dict[str, int]


async def materialize_benchmark_valuation(
    db: InvestmentDB, window: int, default_lookback_days: int
) -> BenchmarkValuationResult:
    """UC0 step 10b (docs/USE_CASES.md) — "define and value the benchmarks
    before valuing invariants": asset_class rows (the 5 BENCHMARK_CLASSES,
    built from constituent `allowed_tickers`) + strategy rows (each enabled
    Strategy's PRIMARY portfolio NAV — the `holds.is_primary` edge is how
    "Strategy's prescribed allocation" resolves to an already-backfilled
    `portfolio_nav` series, judgment call), plus the `real_rate` derived
    signal. Idempotent (re-running replaces same-date rows).

    `window` = the confrontation horizon in trading days, NOT the 756d
    ranking window — this table exists solely as "the pre-materialised
    BENCHMARK that effect.method reads at confrontation"
    (docs/DATA_MODELS.md), so its window is the confrontation's window (see
    `period_series_frame`)."""
    rf = await ratios.load_rf_daily(db)
    derived_signal_rows = await _materialize_real_rates(db, default_lookback_days)

    asset_class_rows: dict[str, int] = {}
    for coarse_class in BENCHMARK_CLASSES:
        nav = await _class_nav(db, coarse_class, rf)
        if nav.empty:
            asset_class_rows[coarse_class] = 0
            continue
        frame = period_series_frame(nav, rf, window)
        asset_class_rows[coarse_class] = await _write_benchmark_series(
            db, BENCHMARK_KIND_ASSET_CLASS, coarse_class, frame
        )

    # Per-asset rows, for `asset:<ticker>` handles (see BENCHMARK_KIND_ASSET).
    asset_rows: dict[str, int] = {}
    for ticker in await investable_tickers(db):
        nav = await _asset_nav(db, ticker, rf)
        if nav.empty:
            asset_rows[ticker] = 0
            continue
        frame = period_series_frame(nav, rf, window)
        asset_rows[ticker] = await _write_benchmark_series(db, BENCHMARK_KIND_ASSET, ticker, frame)

    strategy_rows: dict[str, int] = {}
    strategies = await db.query("SELECT id FROM strategy WHERE enabled = 1 ORDER BY id")
    for row in strategies:
        strategy_id = str(row["id"])
        portfolio_id = await _primary_portfolio_id(db, strategy_id)
        nav = await ratios.load_nav(db, portfolio_id) if portfolio_id else pd.Series(dtype=float)
        if nav.empty:
            strategy_rows[strategy_id] = 0
            continue
        frame = period_series_frame(nav, rf, window)
        strategy_rows[strategy_id] = await _write_benchmark_series(
            db, BENCHMARK_KIND_STRATEGY, strategy_id, frame
        )

    return BenchmarkValuationResult(
        derived_signal_rows, asset_class_rows, strategy_rows, asset_rows
    )


async def _completed_regimes(db: InvestmentDB) -> list[dict[str, Any]]:
    """Historical (closed) Regime instances only — the current, ongoing one
    (`end_date IS NULL`) is not yet a completed period to backtest."""
    return await db.query(
        "SELECT id, regime_type_id, start_date, end_date FROM regime "
        "WHERE end_date IS NOT NULL ORDER BY start_date, id"
    )


@dataclasses.dataclass(frozen=True)
class BacktestsFavorsResult:
    backtests_written: int
    favors_edges: int


async def run_backtests_and_favors(db: InvestmentDB, window: int) -> BacktestsFavorsResult:
    """UC0 step 11 (docs/USE_CASES.md) — one Backtest row per (Strategy,
    completed historical Regime instance) for every RegimeType with
    `>= min_backtest_periods` qualifying instances (system_thresholds), then
    one FAVORS edge per (RegimeType, Strategy) aggregating (mean) across
    that RegimeType's Backtest rows — the same cadence the weekly 08:30 job
    runs, materialized once over the full 35y backfill. Idempotent: Backtest
    ids are deterministic (`f"{strategy_id}:{regime_id}"`, upserted); FAVORS
    has a real composite PK."""
    threshold_rows = await db.query("SELECT key, value FROM system_thresholds")
    thresholds = {r["key"]: r["value"] for r in threshold_rows}
    min_periods = int(thresholds["min_backtest_periods"])

    regimes = await _completed_regimes(db)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in regimes:
        by_type.setdefault(str(r["regime_type_id"]), []).append(r)
    qualifying = {rt: inst for rt, inst in by_type.items() if len(inst) >= min_periods}

    strategy_rows = await db.query("SELECT id FROM strategy WHERE enabled = 1 ORDER BY id")
    strategy_ids = [str(r["id"]) for r in strategy_rows]

    rf = await ratios.load_rf_daily(db)
    nav_cache: dict[str, pd.Series] = {}
    backtests_written = 0
    favors_written = 0

    for regime_type_id, instances in qualifying.items():
        for strategy_id in strategy_ids:
            if strategy_id not in nav_cache:
                portfolio_id = await _primary_portfolio_id(db, strategy_id)
                nav_cache[strategy_id] = (
                    await ratios.load_nav(db, portfolio_id)
                    if portfolio_id
                    else pd.Series(dtype=float)
                )
            nav = nav_cache[strategy_id]
            if nav.empty:
                continue

            per_instance: list[PeriodMetrics] = []
            for instance in instances:
                start = pd.Timestamp(str(instance["start_date"]))
                end = pd.Timestamp(str(instance["end_date"]))
                sliced = nav.loc[(nav.index >= start) & (nav.index <= end)]
                if len(sliced) < 2:
                    continue
                rf_sliced = rf.reindex(sliced.index).ffill()
                metrics = period_metrics(sliced, rf_sliced)
                per_instance.append(metrics)

                regime_days = max((end - start).days, 1)
                covered_days = (sliced.index[-1] - sliced.index[0]).days
                overlap_pct = min(100.0, covered_days / regime_days * 100.0)

                await db.upsert_vertex(
                    "backtest",
                    f"{strategy_id}:{instance['id']}",
                    {
                        "strategy_id": strategy_id,
                        "regime_id": instance["id"],
                        "overlap_pct": overlap_pct,
                        "period": f"{start.date().isoformat()}_{end.date().isoformat()}",
                        "date_start": start.date().isoformat(),
                        "date_end": end.date().isoformat(),
                        "sharpe_rolling": metrics.sharpe_rolling,
                        "sortino_rolling": metrics.sortino_rolling,
                        "calmar_rolling": metrics.calmar_rolling,
                        "max_drawdown": metrics.max_drawdown,
                        "total_return": metrics.total_return,
                        "currency": "USD",
                        "trace": (
                            "Mechanical backtest: strategy's primary-portfolio NAV sliced to "
                            f"historical regime instance {instance['id']} "
                            "(docs/USE_CASES.md UC0 step 11)."
                        ),
                    },
                )
                backtests_written += 1

            if not per_instance:
                continue
            agg = aggregate_metrics(per_instance)
            await db.create_edge(
                "favors",
                regime_type_id,
                strategy_id,
                {
                    "sharpe_rolling": agg.sharpe_rolling,
                    "sortino_rolling": agg.sortino_rolling,
                    "calmar_rolling": agg.calmar_rolling,
                    "max_drawdown": agg.max_drawdown,
                    "n_periods": len(per_instance),
                    "last_updated": date.today().isoformat(),
                },
            )
            favors_written += 1

    return BacktestsFavorsResult(backtests_written, favors_written)
