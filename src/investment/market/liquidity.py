"""GLOBAL_LIQUIDITY composite of M2SL, WALCL, ECBASSETSW, JPNASSETS
(docs/TASKS.md Task 2.2, docs/DATA_MODELS.md 'Composite series').
"""

import pandas as pd

TRAILING_YEARS = 5
_TRAILING_WINDOW = f"{TRAILING_YEARS * 365}D"
# A floor on the trailing sample so an early-history z-score isn't computed
# from a handful of points; not the same thing as a full 5y window (weekly
# and monthly components fill that window at very different observation
# counts) — deliberately generic rather than per-series-tuned (Zen of Python:
# simple, not speculatively precise).
_MIN_PERIODS = 24

# Components denominated in a currency other than USD (docs/TASKS.md Task
# 2.2 "USD-convert"): ECBASSETSW is EUR millions, JPNASSETS is JPY (100M
# units) — both from FRED. M2SL and WALCL are already USD.
_EUR_TICKERS = frozenset({"ECBASSETSW"})
_JPY_TICKERS = frozenset({"JPNASSETS"})


def usd_convert(ticker: str, series: pd.Series, eurusd: pd.Series, usdjpy: pd.Series) -> pd.Series:
    """`eurusd` = USD per EUR (FRED DEXUSEU); `usdjpy` = JPY per USD
    (FRED DEXJPUS). Non-FX-denominated components pass through unchanged."""
    if ticker in _EUR_TICKERS:
        fx = eurusd.reindex(series.index, method="ffill")
        return series * fx
    if ticker in _JPY_TICKERS:
        fx = usdjpy.reindex(series.index, method="ffill")
        return series / fx
    return series


def compute_global_liquidity(components_usd: dict[str, pd.Series]) -> pd.Series:
    """Per component (already USD-converted, see `usd_convert`): z-score
    over a trailing 5y time window. level = 100 + 10 x mean(z_i). Components
    print at different cadences (weekly WALCL/ECBASSETSW, monthly M2SL/
    JPNASSETS) — aligned on the union of their dates, forward-filled (each
    component's latest known-as-of-that-date print, PIT by construction
    since the inputs are already as-known MarketData rows)."""
    aligned = pd.DataFrame(components_usd).sort_index().ffill()
    z_scores = {}
    for name in aligned.columns:
        col = aligned[name]
        mean = col.rolling(_TRAILING_WINDOW, min_periods=_MIN_PERIODS).mean()
        std = col.rolling(_TRAILING_WINDOW, min_periods=_MIN_PERIODS).std(ddof=1)
        z_scores[name] = (col - mean) / std
    mean_z = pd.DataFrame(z_scores).mean(axis=1, skipna=False)
    return 100.0 + 10.0 * mean_z
