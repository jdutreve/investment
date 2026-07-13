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
    periods_per_year: int
    overlap_periods: int
    return_corr: float
    max_gap_sigma: float


def cumulate_returns(returns: pd.Series, base: float = 100.0) -> pd.Series:
    """RATIO-CHAIN a return series into a continuous level series."""
    growth = (1.0 + returns.sort_index()).cumprod()
    return base * growth


def _construct_spliced_level(etf_level: pd.Series, proxy_level: pd.Series) -> pd.Series:
    """RATIO-CHAIN construction only, no validation — `splice_level_series`
    calls this after its own gate passes; `splice_with_resampled_validation`
    calls it directly with NATIVE-resolution data after validating on a
    resampled (coarser) view instead, so a genuinely-daily-but-noisy proxy
    isn't downsampled just because validation needed a coarser lens.

    The join date itself has no own return in either series (the proxy's
    side stops the day before; the ETF's return series starts the day
    AFTER its inception, `.pct_change()` needing a prior ETF price) — a
    one-row gap at the transition, same as any total-return-index splice.
    Preferable to inventing a cross-instrument "return" from two different
    price scales, which is the artifact this whole module exists to
    prevent."""
    etf_level = etf_level.sort_index()
    proxy_level = proxy_level.sort_index()
    join_date = etf_level.index.min()
    proxy_returns = proxy_level.pct_change().dropna()
    etf_returns = etf_level.pct_change().dropna()
    pre_join_returns = proxy_returns.loc[proxy_returns.index < join_date]
    spliced_returns = pd.concat([pre_join_returns, etf_returns])
    return cumulate_returns(spliced_returns)


def splice_level_series(
    ticker: str,
    proxy: str,
    etf_level: pd.Series,
    proxy_level: pd.Series,
    *,
    periods_per_year: int = 252,
) -> tuple[pd.Series, SpliceReport]:
    """`series(t) = proxy total-return for t < ETF.inception, ETF adjusted-
    close return for t >= inception; then cumulate` (TASKS.md). Validates,
    over the overlap window (proxy AND ETF both live, >= 1y): return
    correlation >= 0.95, and no overlap-period |proxy - ETF| return gap
    wider than 3x the pair's own return volatility — raises
    `SpliceArtifactError` otherwise. `periods_per_year` scales the 1y
    overlap floor to the actual sampling frequency of `etf_level`/
    `proxy_level` (252 for daily, 12 for monthly — see
    `splice_with_resampled_validation` for a proxy that's genuinely daily
    but needs a coarser lens to validate).

    The 3-sigma threshold is against the ASSET's return volatility, not the
    gap series' own std: over a long overlap (often 20+ years for these
    proxies), a handful of return outliers is normal — z-scoring the gap
    series against itself flags one every time by pure extreme-value
    statistics (max|z| over ~1000+ samples routinely exceeds 3 even with
    zero artifacts), which is not what this gate is for. Sizing against
    real volatility (typically 1-2%/day) means only a genuine data problem
    (a multi-percent one-period discrepancy — a bad print, a split not
    adjusted) trips it."""
    etf_level = etf_level.sort_index()
    proxy_level = proxy_level.sort_index()
    etf_returns = etf_level.pct_change().dropna()
    proxy_returns = proxy_level.pct_change().dropna()

    overlap_idx = etf_returns.index.intersection(proxy_returns.index)
    min_overlap_obs = int(MIN_OVERLAP_YEARS * periods_per_year)
    if len(overlap_idx) < min_overlap_obs:
        raise SpliceArtifactError(
            f"{ticker}/{proxy}: overlap {len(overlap_idx)} periods below the "
            f"{min_overlap_obs} ({MIN_OVERLAP_YEARS}y @ {periods_per_year}/y) minimum"
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
        periods_per_year=periods_per_year,
        overlap_periods=len(overlap_idx),
        return_corr=corr,
        max_gap_sigma=max_gap_sigma,
    )

    if corr < MIN_RETURN_CORR or max_gap_sigma > MAX_GAP_SIGMA:
        raise SpliceArtifactError(
            f"{ticker}/{proxy}: corr={corr:.3f} (min {MIN_RETURN_CORR}), "
            f"max_gap_sigma={max_gap_sigma:.2f} (max {MAX_GAP_SIGMA}) — rejected"
        )

    return _construct_spliced_level(etf_level, proxy_level), report


def splice_with_resampled_validation(
    ticker: str, proxy: str, etf_level: pd.Series, proxy_level: pd.Series
) -> tuple[pd.Series, SpliceReport]:
    """For a proxy that IS genuinely sampled daily but whose daily RETURNS
    don't compare cleanly against the ETF's (e.g. LBMA's gold fixing, set
    in London hours before the ETF's US market close even opens — verified
    live at M2 build time: GLD vs this exact feed correlates ~0.3-0.65 on
    daily returns but 0.957 at MONTHLY resolution, where the fixing-time
    noise washes out). Validates on both series resampled to month-end, but
    CONSTRUCTS the final series from the NATIVE daily data on both sides —
    unlike a source that's genuinely only monthly (which would need to stay
    monthly pre-join, no granularity to construct from that isn't there),
    here there's only a validation LENS to correct, no data to downsample."""
    etf_monthly = etf_level.sort_index().resample("MS").last().dropna()
    proxy_monthly = proxy_level.sort_index().resample("MS").last().dropna()
    _, report = splice_level_series(ticker, proxy, etf_monthly, proxy_monthly, periods_per_year=12)
    return _construct_spliced_level(etf_level, proxy_level), report
