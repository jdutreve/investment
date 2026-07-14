"""M2 unit tests (docs/MILESTONES.md M2 Definition of Verified) — pure
functions only, no live network. `market/fetcher.py`'s two network calls
(`fetch_yahoo_series`, `fetch_fred_observations`) are exercised indirectly
via `tests/test_db.py`'s injected stub; here we test everything downstream
of a fetch: parsing, transforms, derivatives, composites, and the
HISTORY_PROXIES splice artifact gate.
"""

import numpy as np
import pandas as pd
import pytest

from investment.market import derivatives, fetcher, growth, liquidity, splice


def _dates(start: str, periods: int, freq: str = "D") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=periods, freq=freq)


def _synthetic_returns(n: int, seed: int, mu: float = 0.0003, sigma: float = 0.01) -> np.ndarray:
    return np.random.default_rng(seed).normal(mu, sigma, n)


# -- derivatives.py -----------------------------------------------------


def test_apply_transform_yoy_pct_matches_hand_calc() -> None:
    idx = _dates("2020-01-01", 24, freq="MS")
    series = pd.Series([100.0 + i for i in range(24)], index=idx)
    out = derivatives.apply_transform(series, "yoy_pct")
    assert out.iloc[:12].isna().all()
    assert out.iloc[12] == pytest.approx(12.0)  # (112-100)/100 * 100


def test_apply_transform_none_and_composite_passthrough() -> None:
    series = pd.Series([1.0, 2.0], index=_dates("2020-01-01", 2))
    assert (derivatives.apply_transform(series, "none") == series).all()
    assert (derivatives.apply_transform(series, "composite") == series).all()


def test_apply_transform_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown transform"):
        derivatives.apply_transform(pd.Series([1.0]), "bogus")


def test_compute_derivatives_monthly_observation_lookback() -> None:
    """CPIAUCSL/UNRATE/INDPRO/GROWTH_COMPOSITE: lookback = 1 OBSERVATION,
    not a calendar-day window (docs/DATA_MODELS.md 'MarketData semantics')."""
    idx = _dates("2020-01-01", 5, freq="MS")
    level = pd.Series([5.0, 5.1, 5.3, 5.2, 5.4], index=idx)
    out = derivatives.compute_derivatives(level, "UNRATE", default_lookback_days=30)
    assert pd.isna(out["speed"].iloc[0])
    assert out["speed"].iloc[2] == pytest.approx(5.3 - 5.1)
    assert out["acceleration"].iloc[2] == pytest.approx((5.3 - 5.1) - (5.1 - 5.0))


def test_compute_derivatives_daily_calendar_lookback() -> None:
    idx = _dates("2020-01-01", 90, freq="D")
    level = pd.Series(np.arange(90, dtype=float), index=idx)
    out = derivatives.compute_derivatives(level, "SPY", default_lookback_days=30)
    # contiguous daily index: asof(t-30d) lands exactly 30 rows back
    assert out["speed"].iloc[60] == pytest.approx(30.0)
    assert out["acceleration"].iloc[60] == pytest.approx(0.0)


def test_compute_derivatives_global_liquidity_uses_7d_lookback() -> None:
    idx = _dates("2020-01-01", 40, freq="D")
    level = pd.Series(np.arange(40, dtype=float), index=idx)
    out = derivatives.compute_derivatives(level, "GLOBAL_LIQUIDITY", default_lookback_days=30)
    assert out["speed"].iloc[20] == pytest.approx(7.0)


# -- growth.py ------------------------------------------------------------


def test_growth_composite_formula_and_warm_up() -> None:
    """Regression guard for a real bug: INDPRO and UNRATE are dated at their
    own PUBLICATION date (ADR-003), which falls on a different calendar day
    each month — using the SAME index for both (as an earlier version of
    this test did) hides that mismatch entirely. With realistic,
    independently-dated inputs, a naive Series subtraction leaves the
    composite all-NaN; this locks in the fix (align via ffill onto INDPRO's
    own dates)."""
    idx_indpro = _dates("2000-01-17", 240, freq="MS") + pd.Timedelta(days=16)
    idx_unrate = _dates("2000-01-02", 240, freq="MS") + pd.Timedelta(days=1)
    rng = np.random.default_rng(0)
    indpro_yoy = pd.Series(rng.normal(2.0, 1.0, size=240), index=idx_indpro)
    unrate = pd.Series(rng.normal(5.0, 0.5, size=240), index=idx_unrate)
    out = growth.compute_growth_composite(indpro_yoy, unrate)

    # The composite's cadence follows INDPRO's own (monthly) publication
    # dates, not a union of both series' dates.
    assert list(out.index) == list(idx_indpro)

    # UNRATE's own delta3m() costs 3 extra leading rows on top of the
    # window's warm-up, so the composite's first valid value lands at
    # window+2, not window-1.
    window = growth.TRAILING_YEARS * 12
    assert out.iloc[: window + 2].isna().all()
    assert out.iloc[window + 2 :].notna().all()

    # Independent hand-check via pd.merge_asof — a different pandas
    # mechanism than growth.py's reindex+ffill, not just restating the code
    # under test — then a manual rolling z-score.
    indpro_df = indpro_yoy.rename("indpro_yoy").rename_axis("date").reset_index()
    d3_unrate_df = unrate.diff(3).rename("d3_unrate").rename_axis("date").reset_index()
    merged = pd.merge_asof(indpro_df, d3_unrate_df, on="date").set_index("date")

    i = window + 50
    indpro_window = merged["indpro_yoy"].iloc[i - window + 1 : i + 1]
    z_indpro = (merged["indpro_yoy"].iloc[i] - indpro_window.mean()) / indpro_window.std(ddof=1)
    unrate_window = merged["d3_unrate"].iloc[i - window + 1 : i + 1]
    z_unrate = (merged["d3_unrate"].iloc[i] - unrate_window.mean()) / unrate_window.std(ddof=1)
    expected = 100.0 + 10.0 * (z_indpro - z_unrate) / 2.0
    assert out.iloc[i] == pytest.approx(expected)


# -- liquidity.py ---------------------------------------------------------


def test_usd_convert_eur_and_jpy() -> None:
    idx = _dates("2020-01-01", 3, freq="D")
    eur_series = pd.Series([100.0, 200.0, 300.0], index=idx)
    jpy_series = pd.Series([1000.0, 2000.0, 3000.0], index=idx)
    eurusd = pd.Series([1.1, 1.1, 1.1], index=idx)
    usdjpy = pd.Series([110.0, 110.0, 110.0], index=idx)

    eur_usd = liquidity.usd_convert("ECBASSETSW", eur_series, eurusd, usdjpy)
    jpy_usd = liquidity.usd_convert("JPNASSETS", jpy_series, eurusd, usdjpy)
    unconverted = liquidity.usd_convert("M2SL", eur_series, eurusd, usdjpy)

    assert (eur_usd == eur_series * 1.1).all()
    assert (jpy_usd == jpy_series / 110.0).all()
    assert (unconverted == eur_series).all()


def test_compute_global_liquidity_nan_until_all_components_present() -> None:
    """Mirrors the real floor: WALCL/GLOBAL_LIQUIDITY only exists from
    ~2002 (docs/MILESTONES.md M2 DoV) because one component starts later —
    the composite must not silently ignore a missing component."""
    idx_all = _dates("2015-01-01", 365 * 7, freq="D")
    rng = np.random.default_rng(1)
    components = {
        "M2SL": pd.Series(rng.normal(15000, 100, len(idx_all)), index=idx_all),
        "WALCL": pd.Series(rng.normal(4_000_000, 10_000, len(idx_all)), index=idx_all),
        "ECBASSETSW": pd.Series(rng.normal(4_500_000, 10_000, len(idx_all)), index=idx_all),
    }
    late_idx = idx_all[365:]
    components["JPNASSETS"] = pd.Series(rng.normal(500_000, 5_000, len(late_idx)), index=late_idx)

    out = liquidity.compute_global_liquidity(components)
    assert out.loc[: late_idx[0] - pd.Timedelta(days=1)].isna().all()
    assert out.loc[late_idx[0] + pd.Timedelta(days=400) :].notna().all()


# -- splice.py --------------------------------------------------------------


def test_splice_continuity() -> None:
    """M2 DoV 'splice ARTIFACT gate (#3)': overlap return-corr >=
    MIN_RETURN_CORR and no >3sigma gap at the join; the spliced level
    shows no join spike."""
    idx = _dates("2000-01-01", 252 * 3, freq="B")
    shared_returns = _synthetic_returns(len(idx), seed=42)
    proxy_level = pd.Series(100.0 * np.cumprod(1 + shared_returns), index=idx)

    idio_sigma = 0.0005
    noise = _synthetic_returns(len(idx), seed=7, mu=0.0, sigma=idio_sigma)
    etf_level_full = pd.Series(100.0 * np.cumprod(1 + shared_returns + noise), index=idx)
    etf_level = etf_level_full.iloc[len(idx) // 2 :]  # ETF only "exists" for the back half

    spliced, report = splice.splice_level_series("ETF", "PROXY", etf_level, proxy_level)

    assert report.return_corr >= splice.MIN_RETURN_CORR
    assert report.gap_sigma_p999 <= splice.MAX_GAP_SIGMA
    assert spliced.index.min() == proxy_level.index[1]  # 1st row lost to pct_change()
    assert spliced.index.max() == etf_level.index.max()

    # RATIO-CHAIN (not a level concatenation): every return across the
    # transition is a normal daily move, not an artificial step from a scale
    # mismatch. Checked on the join day ITSELF and the first ETF-side day
    # after it — an earlier version of this test probed only index[1],
    # stepping straight over the one day that was actually broken.
    for day in (etf_level.index[0], etf_level.index[1]):
        assert abs(spliced.pct_change().loc[day]) < 5 * 0.01  # 5x the return sigma


def test_splice_writes_a_row_at_the_join_date() -> None:
    """Regression (found at M4 by the All Weather external check): the splice
    used to emit NO row at the join date. `append_ts_batch` is INSERT OR
    REPLACE and never deletes, so a stale row from an earlier seed run — for
    TLT/IEF/SHY, the RAW un-rescaled ETF price left by seed.py's
    splice-rejected fallback — survived in that hole and injected a ~-91% /
    ~+1000% return pair into every NAV built on those series.

    Scales are deliberately an order of magnitude apart (proxy ~400 vs ETF
    ~35, mirroring the real TLT/VUSTX pair) so that a raw ETF price leaking
    through is unmissable."""
    idx = pd.bdate_range("2000-01-03", periods=600)
    shared = _synthetic_returns(len(idx), seed=5)
    proxy_level = pd.Series(400.0 * np.cumprod(1 + shared), index=idx)
    etf_full = pd.Series(
        35.0 * np.cumprod(1 + shared + _synthetic_returns(len(idx), seed=6, mu=0.0, sigma=3e-4)),
        index=idx,
    )
    etf_level = etf_full.iloc[300:]
    join_date = etf_level.index.min()

    spliced, _ = splice.splice_level_series("ETF", "PROXY", etf_level, proxy_level)

    assert join_date in spliced.index
    # The join row sits on the PROXY's continuous scale, not the ETF's raw
    # one: it is a normal step from the previous day, not a ~-91% cliff.
    prev_level = spliced.loc[spliced.index < join_date].iloc[-1]
    assert spliced.loc[join_date] == pytest.approx(prev_level, rel=0.05)
    # No hole anywhere across the transition.
    assert spliced.loc[join_date:].index.equals(etf_level.index)


def test_splice_rejects_low_correlation() -> None:
    idx = _dates("2000-01-01", 252 * 2, freq="B")
    proxy_level = pd.Series(100.0 * np.cumprod(1 + _synthetic_returns(len(idx), seed=1)), index=idx)
    etf_level = pd.Series(100.0 * np.cumprod(1 + _synthetic_returns(len(idx), seed=2)), index=idx)
    with pytest.raises(splice.SpliceArtifactError):
        splice.splice_level_series("ETF", "PROXY", etf_level, proxy_level)


def test_splice_rejects_insufficient_overlap() -> None:
    idx_proxy = _dates("2000-01-01", 252 * 5, freq="B")
    idx_etf = _dates("2004-06-01", 60, freq="B")
    proxy_level = pd.Series(100.0 + np.arange(len(idx_proxy)), index=idx_proxy)
    etf_level = pd.Series(100.0 + np.arange(len(idx_etf)), index=idx_etf)
    with pytest.raises(splice.SpliceArtifactError, match="overlap"):
        splice.splice_level_series("ETF", "PROXY", etf_level, proxy_level)


def test_splice_rejects_a_gap_artifact() -> None:
    idx = _dates("2000-01-01", 252 * 2, freq="B")
    shared_returns = _synthetic_returns(len(idx), seed=3, sigma=0.001)
    proxy_level = pd.Series(100.0 * np.cumprod(1 + shared_returns), index=idx)
    etf_returns = shared_returns.copy()
    etf_returns[100] += 0.20  # a single-day data artifact, far beyond 3 sigma
    etf_level = pd.Series(100.0 * np.cumprod(1 + etf_returns), index=idx)
    with pytest.raises(splice.SpliceArtifactError, match="gap_sigma_p999"):
        splice.splice_level_series("ETF", "PROXY", etf_level, proxy_level)


def test_splice_gap_check_is_robust_to_a_single_outlier_day() -> None:
    """Mirrors the real TLT/VUSTX and DJP/^BCOM cases verified live at M2
    build time: a clean ~20y daily history (correlation ~0.99) with
    exactly ONE unusually large day (an ETN pricing wobble, say) must NOT
    fail the gate — a single day out of thousands deciding pass/fail off
    the raw max is exactly the extreme-value-statistics trap this module's
    gap metric was already redesigned once to avoid (see
    splice_level_series's docstring); the 99.9th percentile absorbs it."""
    rng = np.random.default_rng(99)
    n = 5000
    shared = rng.normal(0.0003, 0.01, n)
    idio_sigma = 0.001
    proxy_returns = shared + rng.normal(0, idio_sigma, n)
    etf_returns = shared + rng.normal(0, idio_sigma, n)
    etf_returns[2500] += 0.03  # one unusually large day, not a run of bad data

    idx = pd.bdate_range("2000-01-01", periods=n)
    proxy_level = pd.Series(100.0 * np.cumprod(1 + proxy_returns), index=idx)
    etf_level = pd.Series(100.0 * np.cumprod(1 + etf_returns), index=idx)

    _, report = splice.splice_level_series("ETF", "PROXY", etf_level, proxy_level)
    assert report.return_corr >= splice.MIN_RETURN_CORR
    assert report.gap_sigma_p999 <= splice.MAX_GAP_SIGMA


def test_splice_with_resampled_validation_recovers_from_fixing_time_noise() -> None:
    """Mirrors the real LBMA/GLD case verified live at M2 build time: a
    proxy that IS genuinely daily, but whose daily returns are dominated by
    independent same-day noise (a fixing-time mismatch, not a data
    problem) relative to a shared monthly-scale trend. Plain daily
    validation must reject it; resampled validation must accept it AND
    preserve native daily resolution in the constructed series (no
    downsampling)."""
    n_months = 100
    month_starts = pd.date_range("2000-01-01", periods=n_months, freq="MS")
    rng = np.random.default_rng(11)
    monthly_shock = np.cumsum(rng.normal(0.0, 0.06, n_months))

    dates: list[pd.Timestamp] = []
    trend: list[float] = []
    for i, month_start in enumerate(month_starts):
        business_days = pd.date_range(month_start, month_start + pd.offsets.MonthEnd(0), freq="B")
        dates.extend(business_days)
        trend.extend([monthly_shock[i]] * len(business_days))
    idx = pd.DatetimeIndex(dates)
    trend_series = pd.Series(trend, index=idx)

    # Calibrated so daily corr lands well below 0.95 but monthly corr clears
    # it (empirically checked against these exact seeds/params) — the same
    # qualitative gap observed live for GLD vs the real LBMA feed.
    daily_noise_sigma = 0.008
    proxy_level = 100.0 * np.exp(trend_series + rng.normal(0, daily_noise_sigma, len(idx)))
    etf_level_full = 100.0 * np.exp(trend_series + rng.normal(0, daily_noise_sigma, len(idx)))
    etf_level = etf_level_full.iloc[len(idx) // 2 :]

    with pytest.raises(splice.SpliceArtifactError):
        splice.splice_level_series("ETF", "PROXY", etf_level, proxy_level)

    spliced, report = splice.splice_with_resampled_validation(
        "ETF", "PROXY", etf_level, proxy_level
    )
    assert report.periods_per_year == 12
    assert report.return_corr >= splice.MIN_RETURN_CORR

    # Native daily resolution preserved pre-join, not downsampled to monthly.
    join_date = etf_level.index.min()
    pre_join_rows = spliced.loc[:join_date]
    pre_join_months = (join_date.year - proxy_level.index.min().year) * 12 + (
        join_date.month - proxy_level.index.min().month
    )
    assert len(pre_join_rows) > pre_join_months * 5  # business-day density, not monthly


# -- fetcher.py (parsing only — no network) --------------------------------


def test_parse_alfred_first_release_picks_earliest_vintage() -> None:
    """FRED's real `output_type=2` shape (verified live at M2 build time,
    undocumented in FRED's own API examples): WIDE, one row per reference
    date, every non-'date' key named `<series>_<vintage YYYYMMDD>`."""
    observations = [
        {
            "date": "2020-01-01",
            "CPIAUCSL_20200215": "100.0",
            "CPIAUCSL_20200320": "100.5",  # later revision — must be ignored
        },
        {"date": "2020-02-01", "CPIAUCSL_20200314": "101.0"},
        {"date": "2020-03-01", "CPIAUCSL_20200410": "."},  # missing — skipped
    ]
    out = fetcher.parse_alfred_first_release(observations)
    assert len(out) == 2
    assert out.loc[pd.Timestamp("2020-02-15")] == 100.0  # first release, not the revision
    assert out.loc[pd.Timestamp("2020-03-14")] == 101.0


def test_parse_fred_current_dates_by_lag_not_realtime_start() -> None:
    """Regression guard for a real bug: `output_type=1`'s `realtime_start`
    is the SAME snapshot date (today) on every row regardless of the
    observation's own reference date — verified live at M2 build time.
    Using it as the dating key collapsed the whole series onto one row.
    The only usable dating here is reference date + availability_lag_days."""
    observations = [
        {"date": "2020-01-01", "realtime_start": "2026-07-13", "value": "5.0"},
        {"date": "2020-02-01", "realtime_start": "2026-07-13", "value": "5.2"},
    ]
    out = fetcher.parse_fred_current(observations, lag_days=7)
    assert len(out) == 2
    assert out.loc[pd.Timestamp("2020-01-08")] == 5.0  # 2020-01-01 + 7d
    assert out.loc[pd.Timestamp("2020-02-08")] == 5.2  # 2020-02-01 + 7d


def test_parse_fred_current_skips_missing_values() -> None:
    observations = [{"date": "2020-01-01", "realtime_start": "2020-01-02", "value": "."}]
    assert fetcher.parse_fred_current(observations, lag_days=0).empty


def test_forward_fill_gaps_respects_max_days() -> None:
    idx = pd.date_range("2020-01-01", periods=8)
    series = pd.Series([1.0, *([np.nan] * 6), 2.0], index=idx)
    out = fetcher.forward_fill_gaps(series, max_days=5)
    assert out.iloc[1:6].tolist() == [1.0] * 5
    assert pd.isna(out.iloc[6])
    assert out.iloc[7] == 2.0


def test_parse_lbma_gold_json_uses_usd_and_skips_null() -> None:
    """Real LBMA feed shape: `{"d": "...", "v": [usd, gbp, eur]}` — eur is
    null before 1999; a day with a null USD (holiday placeholder) is
    skipped rather than stored as 0.0."""
    raw = (
        b'[{"is_cms_locked":0,"d":"1968-01-02","v":[35.18,14.64,null]},'
        b'{"is_cms_locked":0,"d":"1968-01-03","v":[35.16,14.62,null]},'
        b'{"is_cms_locked":0,"d":"1999-01-04","v":[287.85,173.5,null]},'
        b'{"is_cms_locked":0,"d":"2020-01-06","v":[null,null,null]}]'
    )
    out = fetcher.parse_lbma_gold_json(raw)
    assert len(out) == 3
    assert out.loc[pd.Timestamp("1968-01-02")] == 35.18
    assert out.loc[pd.Timestamp("1999-01-04")] == 287.85
    assert pd.Timestamp("2020-01-06") not in out.index
