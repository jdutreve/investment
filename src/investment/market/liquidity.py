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

# THE FOUR COMPONENTS, named once. `mechanical/catchup.py` and `seed.py` each
# carried their own copy of this tuple, and the freshness line in the digest
# would have been the third — so it lives beside the function that combines
# them. What the composite is NOT is part of its name everywhere it is shown:
# there is no China, and it mixes a money stock with three balance sheets.
COMPONENTS: tuple[str, ...] = ("M2SL", "WALCL", "ECBASSETSW", "JPNASSETS")
PROXY_DESCRIPTION = "US M2 + Fed/ECB/BoJ balance sheets"

# THE FOUR STATES, and the reason there are four rather than two.
#
# The composite has a STOCK (is liquidity abundant against its own 5y norm) and
# a MOVEMENT (is it improving), and the two are independent. Naming the state
# from one dimension names half of it — which is what three separate readings of
# this series each did differently until 2026-08-30: `derive_tags` demanded that
# both agree and so left the two MIXED states unnamed; `derive_events` called it
# tightening or easing on the sign of `speed` alone; DATA_MODELS defined
# expansion and contraction on the level alone. A level of 105 falling was
# narrated "tightening" and tagged nothing at all.
#
# The mixed states are not an edge case to tidy away — they are the transitions,
# the only two states in which the stock and the flow disagree, and the ones
# worth seeing coming.
#
# TOTAL BY CONSTRUCTION: every (level, speed) pair maps to exactly one state,
# which is the property the old tag rule lacked. Two boundary conventions make
# it so, and both are deliberate:
#   - `level >= 100` counts as abundant. 100 is mean-z zero, the norm itself,
#     and a series sitting exactly on its norm is not scarce.
#   - `speed > 0` counts as improving, so a FLAT composite reads as fading or
#     restrictive rather than as support. Conservative on purpose: standing
#     still is not a tailwind, and this project would rather understate one.
SUPPORTIVE = "liquidity-supportive"
FADING = "liquidity-fading"
REPAIRING = "liquidity-repairing"
RESTRICTIVE = "liquidity-restrictive"
STATES: tuple[str, ...] = (SUPPORTIVE, FADING, REPAIRING, RESTRICTIVE)
STATE_READINGS: dict[str, str] = {
    SUPPORTIVE: "abundant and improving",
    FADING: "abundant but deteriorating",
    REPAIRING: "scarce but improving",
    RESTRICTIVE: "scarce and deteriorating",
}


def liquidity_state(level: float | None, speed: float | None) -> str | None:
    """The composite's state from its stock AND its movement, or None when
    either is missing (an early history, or a component that never arrived).

    THE ONE DEFINITION. Everything that names this series reads it here — the
    Regime tag, the narrative event, the digest, the dashboard — because four
    places naming it independently is exactly how they came to disagree."""
    if level is None or speed is None:
        return None
    if level >= 100.0:
        return SUPPORTIVE if speed > 0.0 else FADING
    return REPAIRING if speed > 0.0 else RESTRICTIVE


def level_in_sigma(level: float) -> float:
    """The level restated as what it measures: mean component z-score.

    `level = 100 + 10 x mean(z)` is an index whose units nobody carries in their
    head, so 95.8 reads as "a bit under 100" rather than as "the components
    average 0.42 standard deviations under their own five-year norm". Displaying
    the index without this translation is displaying an arbitrary scale."""
    return (level - 100.0) / 10.0


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
