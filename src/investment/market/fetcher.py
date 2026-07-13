"""Market fetcher (docs/TASKS.md Task 2.1) — Yahoo Finance + FRED/ALFRED.

Fetch primitives only; the 35y backfill / catch-up orchestration (which
tickers, which start dates, splice wiring) lives in the caller (UC0 seed
step 9 for now — docs/USE_CASES.md; the Monday catch-up job arrives at M9).

As-known-at-ts (ADR-003): `fetch_fred_series` dispatches revised series
(INDPRO, CPIAUCSL, UNRATE) to the ALFRED first-release path — indexed at
their true publication date (`realtime_start`) — and everything else to the
current-vintage path (non-revised in practice: ETF prices, T10Y2Y, WALCL and
the other liquidity components — ADR-003 consequences), dated at
`realtime_start` with an `availability_lag_days` fallback.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, timedelta
from typing import Any

import aiohttp
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_TIMEOUT_SECONDS = 30.0

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SECONDS = 60.0
YAHOO_RATE_LIMIT_SECONDS = 0.5

# Meaningfully-revised FRED series (ADR-003) — backfilled via ALFRED
# first-release vintages. Everything else uses the current vintage, which
# for these series (non-revised in practice) IS the first release.
REVISED_SERIES = frozenset({"INDPRO", "CPIAUCSL", "UNRATE"})


async def _with_retry[T](fn: Callable[[], Awaitable[T]], *, label: str) -> T:
    """3 attempts, exponential backoff off a 60s base (docs/TASKS.md Task
    2.1) — the caller decides the missing-data fallback on final failure."""
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "fetch failed (%s), attempt %d/%d: %s", label, attempt + 1, RETRY_ATTEMPTS, exc
            )
            if attempt < RETRY_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_BACKOFF_BASE_SECONDS * (2**attempt))
    assert last_exc is not None
    raise last_exc


def _to_naive_date_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def _series_from_date_map(rows: dict[date, float]) -> pd.Series:
    if not rows:
        return pd.Series(dtype=float)
    dates = sorted(rows)
    return pd.Series([rows[d] for d in dates], index=pd.DatetimeIndex(dates), dtype=float)


# -- Yahoo -------------------------------------------------------------------


def _fetch_yahoo_sync(ticker: str, start: date | None) -> pd.Series:
    kwargs: dict[str, object] = {"start": start.isoformat()} if start else {"period": "max"}
    df = yf.Ticker(ticker).history(auto_adjust=True, **kwargs)
    if df.empty:
        raise ValueError(f"no Yahoo data for {ticker!r}")
    idx = _to_naive_date_index(pd.DatetimeIndex(df.index))
    series = pd.Series(df["Close"].to_numpy(), index=idx, name=ticker)
    return series[~series.index.duplicated(keep="last")].sort_index()


async def fetch_yahoo_series(ticker: str, start: date | None = None) -> pd.Series:
    """Adjusted close (dividends/splits folded in via `auto_adjust`), USD
    (or the pair's own quote currency for FX tickers like CHFUSD=X)."""

    async def _call() -> pd.Series:
        return await asyncio.get_running_loop().run_in_executor(
            None, _fetch_yahoo_sync, ticker, start
        )

    return await _with_retry(_call, label=f"yahoo:{ticker}")


# -- FRED / ALFRED ------------------------------------------------------------


async def fetch_fred_observations(
    series_id: str, api_key: str, *, output_type: int, observation_start: str
) -> list[dict[str, str]]:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "output_type": str(output_type),
        "observation_start": observation_start,
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
    }

    async def _call() -> list[dict[str, str]]:
        timeout = aiohttp.ClientTimeout(total=FRED_TIMEOUT_SECONDS)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(FRED_BASE_URL, params=params) as resp,
        ):
            resp.raise_for_status()
            data = await resp.json()
            return list(data["observations"])

    return await _with_retry(_call, label=f"fred:{series_id}")


def parse_fred_current(observations: list[dict[str, str]], lag_days: int) -> pd.Series:
    """Current-vintage series, dated at `realtime_start` (fallback:
    reference date + `lag_days` — ADR-003)."""
    rows: dict[date, float] = {}
    for obs in observations:
        if obs["value"] == ".":
            continue
        ref_date = date.fromisoformat(obs["date"])
        pub_raw = obs.get("realtime_start")
        pub_date = date.fromisoformat(pub_raw) if pub_raw else ref_date + timedelta(days=lag_days)
        rows[pub_date] = float(obs["value"])
    return _series_from_date_map(rows)


def parse_alfred_first_release(observations: list[dict[str, str]]) -> pd.Series:
    """`output_type=2` returns one row per (reference date, vintage) pair —
    every value change ever recorded. The first release per reference date
    is the row with the EARLIEST `realtime_start`; that `realtime_start` IS
    the true publication date (ADR-003)."""
    by_ref: dict[date, tuple[date, float]] = {}
    for obs in observations:
        if obs["value"] == ".":
            continue
        ref_date = date.fromisoformat(obs["date"])
        pub_date = date.fromisoformat(obs["realtime_start"])
        value = float(obs["value"])
        earliest = by_ref.get(ref_date)
        if earliest is None or pub_date < earliest[0]:
            by_ref[ref_date] = (pub_date, value)
    return _series_from_date_map({pub: value for pub, value in by_ref.values()})


async def fetch_fred_series(series_id: str, api_key: str, start: date, lag_days: int) -> pd.Series:
    observation_start = start.isoformat()
    if series_id in REVISED_SERIES:
        obs = await fetch_fred_observations(
            series_id, api_key, output_type=2, observation_start=observation_start
        )
        return parse_alfred_first_release(obs)
    obs = await fetch_fred_observations(
        series_id, api_key, output_type=1, observation_start=observation_start
    )
    return parse_fred_current(obs, lag_days)


# -- shared --------------------------------------------------------------


def forward_fill_gaps(series: pd.Series, max_days: int = 5) -> pd.Series:
    """Missing-data convention (docs/DATA_MODELS.md 'Calculation
    conventions'): forward-fill up to `max_days` consecutive gaps; longer
    gaps are left as NaN for the caller to abort + ErrorEvent on."""
    return series.sort_index().ffill(limit=max_days)


async def fetch_raw_series(
    ticker_row: Mapping[str, Any], api_key: str, start: date | None
) -> pd.Series:
    """Dispatch by `allowed_tickers.source` — the shape UC0 seed step 9 and
    the future Monday catch-up job both loop over."""
    source = ticker_row["source"]
    ticker = str(ticker_row["ticker"])
    if source == "yahoo":
        return await fetch_yahoo_series(ticker, start)
    if source == "fred":
        lag_days = int(ticker_row.get("availability_lag_days") or 0)
        return await fetch_fred_series(ticker, api_key, start or date(1900, 1, 1), lag_days)
    raise ValueError(f"unsupported source for raw fetch: {source!r}")
