"""M5 unit tests (docs/MILESTONES.md M5 Definition of Verified) — pure
functions in `mechanical/backtests.py` only, no DB."""

import numpy as np
import pandas as pd
import pytest

from investment.mechanical import backtests, ratios


def test_blended_class_nav_starts_at_100_and_reaches_earliest_constituent() -> None:
    """EEM-style late joiner (starts 2003) must not truncate the class NAV
    to 2003 when SPY-style tickers reach back further — the whole point of
    equal-weighting AVAILABLE constituents rather than requiring all of
    them (docs/MILESTONES.md M5 DoV: benchmark_valuation asset_class rows)."""
    idx_long = pd.bdate_range("2020-01-02", periods=10)
    idx_short = idx_long[5:]  # joins 5 days in
    prices = {
        "SPY": pd.Series(
            [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0], index=idx_long
        ),
        "EEM": pd.Series([50.0, 51.0, 52.0, 53.0, 54.0], index=idx_short),
    }
    nav = backtests.blended_class_nav(prices)
    assert nav.index[0] == idx_long[1]  # first date with a computable return
    assert nav.iloc[0] == pytest.approx(100.0)
    # Before EEM joins, the blended return equals SPY's own return alone.
    spy_return_day2 = prices["SPY"].iloc[2] / prices["SPY"].iloc[1] - 1.0
    assert nav.iloc[1] == pytest.approx(100.0 * (1.0 + spy_return_day2))


def test_blended_class_nav_empty_without_constituents() -> None:
    assert backtests.blended_class_nav({}).empty


def test_cash_class_nav_matches_rf_compounding() -> None:
    idx = pd.bdate_range("2021-01-04", periods=5)
    rf = pd.Series([0.0, 0.001, 0.001, 0.001, 0.001], index=idx)
    nav = backtests.cash_class_nav(rf)
    assert nav.iloc[0] == pytest.approx(100.0)
    # day0's return is zeroed (NAV(t0)=100 convention), so only the 4
    # subsequent days (index 1-4) compound at 0.001 each.
    assert nav.iloc[-1] == pytest.approx(100.0 * 1.001**4)


def test_period_metrics_short_slice_returns_none() -> None:
    idx = pd.bdate_range("2021-01-04", periods=1)
    nav = pd.Series([100.0], index=idx)
    rf = pd.Series([0.0], index=idx)
    metrics = backtests.period_metrics(nav, rf)
    assert metrics == backtests.PeriodMetrics(None, None, None, None, None)


def test_period_metrics_matches_whole_slice_hand_computation() -> None:
    """window = len(nav) makes the 'rolling' functions cover exactly the
    slice — total_return must equal the closed-form NAV(t)/NAV(0) - 1."""
    idx = pd.bdate_range("2021-01-04", periods=20)
    rng = np.random.default_rng(0)
    nav = pd.Series(100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.01, 20)), index=idx)
    rf = pd.Series(0.0, index=idx)
    metrics = backtests.period_metrics(nav, rf)
    expected_total_return = nav.iloc[-1] / nav.iloc[0] - 1.0
    assert metrics.total_return == pytest.approx(expected_total_return)
    assert metrics.max_drawdown is not None and metrics.max_drawdown <= 0.0


def test_aggregate_metrics_is_simple_mean_ignoring_none() -> None:
    rows = [
        backtests.PeriodMetrics(1.0, 1.0, 1.0, -0.1, 0.1),
        backtests.PeriodMetrics(2.0, None, 3.0, -0.2, 0.2),
    ]
    agg = backtests.aggregate_metrics(rows)
    assert agg.sharpe_rolling == pytest.approx(1.5)
    assert agg.sortino_rolling == pytest.approx(1.0)  # the only non-None value
    assert agg.calmar_rolling == pytest.approx(2.0)
    assert agg.max_drawdown == pytest.approx(-0.15)


def test_aggregate_metrics_empty_field_all_none() -> None:
    agg = backtests.aggregate_metrics([backtests.PeriodMetrics(None, None, None, None, None)])
    assert agg == backtests.PeriodMetrics(None, None, None, None, None)


def test_period_series_frame_resamples_weekly_and_return_is_trailing_window() -> None:
    idx = pd.bdate_range("2021-01-04", periods=40)
    nav = pd.Series(100.0 * (1.0 + 0.001) ** np.arange(40), index=idx)
    rf = pd.Series(0.0, index=idx)
    frame = backtests.period_series_frame(nav, rf, window=10)
    assert list(frame.columns) == ["return", "sortino_rolling", "max_drawdown", "volatility"]
    # Weekly anchors: at most one row per calendar week.
    assert frame.index.is_monotonic_increasing
    assert (frame.index.to_series().diff().dropna().dt.days >= 4).all()
    last_return = ratios.rolling_total_return(nav, 10).iloc[-1]
    assert frame["return"].iloc[-1] == pytest.approx(last_return)
