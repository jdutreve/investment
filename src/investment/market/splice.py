"""HISTORY_PROXIES splice (docs/TASKS.md HISTORY_PROXIES 'SPLICE RULE' +
'ARTIFACT GATE #3', docs/MILESTONES.md M2 DoV): extend a tradable ETF's
level series back through a longer-history proxy, in RETURNS space, RATIO-
CHAINED so there is no artificial step at the join. A failing pair is
rejected (`SpliceArtifactError`) rather than silently splicing an artifact —
the caller falls back to a shorter (ETF-only) floor.
"""

from dataclasses import dataclass

import pandas as pd

MIN_OVERLAP_YEARS = 1.0
MIN_RETURN_CORR = 0.95
MAX_GAP_SIGMA = 3.0


class SpliceArtifactError(RuntimeError):
    """The proxy/ETF pair failed the return-correlation or gap gate."""


@dataclass(frozen=True)
class SpliceReport:
    ticker: str
    proxy: str
    join_date: str
    overlap_days: int
    return_corr: float
    max_gap_sigma: float


def cumulate_returns(returns: pd.Series, base: float = 100.0) -> pd.Series:
    """RATIO-CHAIN a return series into a continuous level series."""
    growth = (1.0 + returns.sort_index()).cumprod()
    return base * growth


def splice_level_series(
    ticker: str, proxy: str, etf_level: pd.Series, proxy_level: pd.Series
) -> tuple[pd.Series, SpliceReport]:
    """`series(t) = proxy total-return for t < ETF.inception, ETF adjusted-
    close return for t >= inception; then cumulate` (TASKS.md). Validates,
    over the overlap window (proxy AND ETF both live, >= 1y): daily-return
    correlation >= 0.95, and no overlap-day |proxy - ETF| return gap wider
    than 3x the pair's own daily-return volatility — raises
    `SpliceArtifactError` otherwise.

    The 3-sigma threshold is against the ASSET's return volatility, not the
    gap series' own std: over a long overlap (often 20+ years for these
    proxies), a handful of daily tracking-error outliers is normal —
    z-scoring the gap series against itself flags one every time by pure
    extreme-value statistics (max|z| over ~1000+ samples routinely exceeds
    3 even with zero artifacts), which is not what this gate is for. Sizing
    against real daily volatility (typically 1-2%) means only a genuine
    data problem (a multi-percent one-day discrepancy — a bad print, a
    split not adjusted) trips it."""
    etf_level = etf_level.sort_index()
    proxy_level = proxy_level.sort_index()
    etf_returns = etf_level.pct_change().dropna()
    proxy_returns = proxy_level.pct_change().dropna()

    overlap_idx = etf_returns.index.intersection(proxy_returns.index)
    min_overlap_obs = int(MIN_OVERLAP_YEARS * 252)
    if len(overlap_idx) < min_overlap_obs:
        raise SpliceArtifactError(
            f"{ticker}/{proxy}: overlap {len(overlap_idx)}d below the "
            f"{min_overlap_obs}d ({MIN_OVERLAP_YEARS}y) minimum"
        )

    etf_overlap = etf_returns.loc[overlap_idx]
    proxy_overlap = proxy_returns.loc[overlap_idx]
    corr = float(etf_overlap.corr(proxy_overlap))

    gap = (proxy_overlap - etf_overlap).abs()
    sigma = pd.concat([etf_overlap, proxy_overlap]).std(ddof=1)
    max_gap_sigma = float(gap.max() / sigma) if sigma > 0 else float("inf")

    join_date = etf_level.index.min()
    report = SpliceReport(
        ticker=ticker,
        proxy=proxy,
        join_date=str(join_date.date()),
        overlap_days=len(overlap_idx),
        return_corr=corr,
        max_gap_sigma=max_gap_sigma,
    )

    if corr < MIN_RETURN_CORR or max_gap_sigma > MAX_GAP_SIGMA:
        raise SpliceArtifactError(
            f"{ticker}/{proxy}: corr={corr:.3f} (min {MIN_RETURN_CORR}), "
            f"max_gap_sigma={max_gap_sigma:.2f} (max {MAX_GAP_SIGMA}) — rejected"
        )

    # The join date itself has no own return in either series (the proxy's
    # side stops the day before; `etf_returns` starts the day AFTER ETF
    # inception, `.pct_change()` needing a prior ETF price) — a one-row gap
    # at the transition, same as any total-return-index splice. Preferable
    # to inventing a cross-instrument "return" from two different price
    # scales, which is the artifact this gate exists to prevent.
    pre_join_returns = proxy_returns.loc[proxy_returns.index < join_date]
    spliced_returns = pd.concat([pre_join_returns, etf_returns])
    return cumulate_returns(spliced_returns), report
