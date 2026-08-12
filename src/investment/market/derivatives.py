"""Transforms + level/speed/acceleration (docs/TASKS.md Task 2.2).

`level` is whatever docs/DATA_MODELS.md "MarketData semantics" pins for the
series (adjusted close, raw rate, or a YoY-transformed macro reading);
`speed`/`acceleration` are 1st/2nd differences over a per-series lookback —
one OBSERVATION for monthly macro series (a calendar lookback would be
trivially satisfied every day), a number of CALENDAR DAYS for daily series.
"""

from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd

# Monthly-observation series (docs/DATA_MODELS.md "MarketData semantics"):
# derivative lookback = 1 observation, not a calendar-day window.
MONTHLY_OBSERVATION_TICKERS = frozenset({"CPIAUCSL", "UNRATE", "INDPRO", "GROWTH_COMPOSITE"})

# Per-ticker CALENDAR-DAY lookback overrides for speed/acceleration, for
# daily series whose natural derivative window is neither 1 observation nor
# the generic short default:
#   - GLOBAL_LIQUIDITY blends weekly/monthly components but is itself sampled
#     at whatever cadence its components print (docs/DATA_MODELS.md).
#   - gold_10y_dev's `speed` IS the owner's defined momentum leg,
#     `speed = D - D[-6 months]` (~182 calendar days), not the 30-day default:
#     the invariant's "& rising" predicate is a 6-month impulse, not a
#     one-month wiggle (db/seed_data.py inv-gold-ratio-trend-tilt).
WEEKLY_LOOKBACK_DAYS_TICKERS: dict[str, int] = {"GLOBAL_LIQUIDITY": 7, "gold_10y_dev": 182}


def apply_transform(series: pd.Series, transform: str) -> pd.Series:
    """'none' | 'yoy_pct' (12-observation percent change) | 'composite'
    (passthrough — composites are computed directly at their pinned level,
    docs/TASKS.md Task 2.2)."""
    if transform in ("none", "composite"):
        return series
    if transform == "yoy_pct":
        return series.pct_change(periods=12) * 100.0
    raise ValueError(f"unknown transform: {transform!r}")


def _asof_lag(level: pd.Series, days: int) -> pd.Series:
    """Value as of (t - days) for every t in `level`'s index, via the latest
    known observation at or before that date — the calendar-day analogue of
    `.diff(1)` for a series that isn't evenly spaced (weekends/holidays)."""
    idx = level.index.values
    target = idx - np.timedelta64(days, "D")
    pos = np.searchsorted(idx, target, side="right") - 1
    values = level.to_numpy()
    out = np.full(len(level), np.nan)
    valid = pos >= 0
    out[valid] = values[pos[valid]]
    return pd.Series(out, index=level.index)


def compute_derivatives(level: pd.Series, ticker: str, default_lookback_days: int) -> pd.DataFrame:
    """level, speed (1st diff over the per-series lookback), acceleration
    (diff of speed over the SAME lookback — docs/DATA_MODELS.md: CPIAUCSL
    'speed = delta1m of YoY, accel = delta of speed')."""
    level = level.sort_index()
    if ticker in MONTHLY_OBSERVATION_TICKERS:
        speed = level.diff(1)
        acceleration = speed.diff(1)
    else:
        days = WEEKLY_LOOKBACK_DAYS_TICKERS.get(ticker, default_lookback_days)
        speed = level - _asof_lag(level, days)
        acceleration = speed - _asof_lag(speed, days)
    return pd.DataFrame({"level": level, "speed": speed, "acceleration": acceleration})


def market_data_rows(
    ticker: str, asset_class: str, currency: str, deriv: pd.DataFrame, start: date | None
) -> list[dict[str, Any]]:
    """A `compute_derivatives` frame as `market_data` rows, optionally truncated
    to `start`.

    Lives HERE rather than beside either producer, because there are two and they
    must write the same shape: the UC0 seed's 35-year backfill and the Monday
    catch-up's bounded refresh (mechanical/catchup.py). It was a private helper
    in `seed.py` until the catch-up needed it — the moment a second caller
    arrives is the moment a private helper becomes a contract (CLAUDE.md: "WHEN A
    SECOND ONE ARRIVES").

    NaN -> None so the columns arrive as SQL NULL: `speed`/`acceleration` are NaN
    over the warm-up of every series, and a stored NaN reads back as a float that
    every comparison silently fails."""
    df = deriv if start is None else deriv[deriv.index >= pd.Timestamp(start)]
    df = df.astype(object).where(pd.notna(df), None)
    # pandas-stubs types to_dict("records") keys as Hashable (the general
    # case); every column here is a plain string (level/speed/acceleration).
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
