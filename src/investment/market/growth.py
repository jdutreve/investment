"""GROWTH_COMPOSITE — the 4 Seasons growth axis (docs/TASKS.md Task 2.2;
replaces ISM PMI, which has no free perennial source — IMPROVEMENTS I-20).
"""

import pandas as pd

TRAILING_YEARS = 10
_MONTHLY_OBS_PER_YEAR = 12


def _zscore_rolling(series: pd.Series, window_obs: int) -> pd.Series:
    """Trailing z-score in OBSERVATION count (both inputs are monthly
    series, one row per calendar month) — `min_periods=window_obs` so the
    composite is NaN until a full trailing window exists, never a
    partially-formed z-score."""
    mean = series.rolling(window_obs, min_periods=window_obs).mean()
    std = series.rolling(window_obs, min_periods=window_obs).std(ddof=1)
    return (series - mean) / std


def compute_growth_composite(indpro_yoy: pd.Series, unrate: pd.Series) -> pd.Series:
    """z(INDPRO YoY, 10y trailing) - z(delta3m UNRATE, 10y trailing), halved,
    rebased: level = 100 + 10 x raw. >100 expansion, <100 contraction.
    `unrate` is the raw rate (percent points), not YoY-transformed —
    docs/DATA_MODELS.md 'MarketData semantics'.

    INDPRO and UNRATE are indexed at their own PUBLICATION date (ADR-003),
    which falls on a different calendar day each month (BLS releases them
    separately) — a raw Series subtraction would align by exact date and
    find almost no matches, leaving the composite NaN nearly everywhere.
    UNRATE's freshest known reading is forward-filled onto INDPRO's own
    (monthly) publication dates instead: PIT-correct (never looks ahead)
    and keeps the composite's cadence at 1 row/month, matching derivatives.py
    MONTHLY_OBSERVATION_TICKERS' 1-observation lookback for this ticker."""
    indpro_yoy = indpro_yoy.sort_index()
    delta3m_unrate = unrate.sort_index().diff(3)
    delta3m_unrate_aligned = (
        delta3m_unrate.reindex(delta3m_unrate.index.union(indpro_yoy.index))
        .sort_index()
        .ffill()
        .reindex(indpro_yoy.index)
    )

    window = TRAILING_YEARS * _MONTHLY_OBS_PER_YEAR
    z_indpro = _zscore_rolling(indpro_yoy, window)
    z_unrate = _zscore_rolling(delta3m_unrate_aligned, window)
    raw = (z_indpro - z_unrate) / 2.0
    return 100.0 + 10.0 * raw
