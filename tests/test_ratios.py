"""M4 unit tests (docs/MILESTONES.md M4 Definition of Verified) — pure
functions only, no DB. `test_nav_conventions_golden` is the named test in
docs/TASKS.md Phase 8's integration-test list ("NAV/sharpe/sortino/calmar on
a fixed 3-asset fixture == pinned golden numbers"); the other tests here
isolate individual pieces of the same pinned conventions
(docs/DATA_MODELS.md "Calculation conventions").
"""

import numpy as np
import pandas as pd
import pytest

from investment.mechanical import ratios


def _closed_form_nav_no_rebalance(
    weights: dict[str, float], prices: dict[str, pd.Series], rf: pd.Series
) -> pd.Series:
    """Independent reference for a window that never crosses a month
    boundary (no rebalance fires): each sleeve simply tracks its own asset's
    cumulative return from t0; cash compounds at rf_daily. Structurally
    different from `synthesize_nav`'s day-by-day sleeve loop (a closed-form
    ratio vs. an iterative simulation), so agreement is a real check, not a
    restatement — mirrors the independent-recomputation style of
    `test_market.py::test_growth_composite_formula_and_warm_up`."""
    non_cash = [t for t in weights if t != "cash"]
    total = sum(100.0 * weights[t] * (prices[t] / prices[t].iloc[0]) for t in non_cash)
    if "cash" in weights:
        rf_growth = (1.0 + rf).cumprod() / (1.0 + rf.iloc[0])
        total = total + 100.0 * weights["cash"] * rf_growth
    return total


# -- synthesize_nav ---------------------------------------------------------


def test_synthesize_nav_matches_closed_form_within_one_month() -> None:
    idx = pd.bdate_range("2021-01-04", periods=6)  # all within January — no rebalance
    prices = {
        "A": pd.Series([100.0, 101.0, 102.0, 101.0, 103.0, 104.0], index=idx),
        "B": pd.Series([50.0, 49.5, 50.5, 50.0, 50.2, 50.5], index=idx),
    }
    rf = ratios.rf_daily(pd.Series([2.0] * 6, index=idx))
    weights = {"A": 0.5, "B": 0.3, "cash": 0.2}

    nav = ratios.synthesize_nav(weights, prices, rf)
    expected = _closed_form_nav_no_rebalance(weights, prices, rf)

    assert nav.iloc[0] == pytest.approx(100.0)
    pd.testing.assert_series_equal(nav, expected, check_names=False)


def test_synthesize_nav_rebalances_on_first_trading_day_of_month() -> None:
    """B is a 0%-return control asset: the day-1 return then isolates
    exactly what weight A's move was applied at. If day1 used the DRIFTED
    (pre-rebalance) weights the numbers below would differ from the
    hand-computed 115.0/121.5."""
    idx = pd.DatetimeIndex(["2021-01-29", "2021-02-01", "2021-02-02"])
    prices = {
        "A": pd.Series([100.0, 130.0, 143.0], index=idx),
        "B": pd.Series([100.0, 100.0, 100.0], index=idx),
    }
    rf = pd.Series([0.0, 0.0, 0.0], index=idx)
    weights = {"A": 0.5, "B": 0.5}

    nav = ratios.synthesize_nav(weights, prices, rf)

    assert nav.iloc[0] == pytest.approx(100.0)
    # Rebalance fires on 02-01 (first trading day of Feb): 50/50 of the
    # 01-29 total (100) grows by that day's return -> 0.5*130 + 0.5*100 = 115.
    assert nav.iloc[1] == pytest.approx(115.0)
    # No rebalance on 02-02: sleeves drift from their post-rebalance values
    # (65/50) -> 65*1.10 + 50*1.0 = 121.5.
    assert nav.iloc[2] == pytest.approx(121.5)


def test_synthesize_nav_empty_when_no_overlap() -> None:
    idx_a = pd.bdate_range("2020-01-01", periods=5)
    idx_b = pd.bdate_range("2021-01-01", periods=5)
    prices = {
        "A": pd.Series(100.0, index=idx_a),
        "B": pd.Series(100.0, index=idx_b),
    }
    rf = pd.Series(0.0, index=idx_a.union(idx_b))
    nav = ratios.synthesize_nav({"A": 0.5, "B": 0.5}, prices, rf)
    assert nav.empty


# -- rolling indicators -------------------------------------------------


def test_rolling_indicators_golden_numbers() -> None:
    idx = pd.bdate_range("2021-03-01", periods=5)
    nav = pd.Series([100.0, 101.0, 99.0, 102.0, 101.0], index=idx)
    returns = ratios.daily_returns(nav)
    rf = pd.Series([0.0001] * 5, index=idx)
    window = 5

    sharpe = ratios.rolling_sharpe(returns, rf, window).iloc[-1]
    sortino = ratios.rolling_sortino(returns, rf, window).iloc[-1]
    mdd_series = ratios.rolling_max_drawdown(nav, window)
    mdd = mdd_series.iloc[-1]
    calmar = ratios.rolling_calmar(nav, mdd_series, window).iloc[-1]
    vol = ratios.rolling_volatility(returns, window).iloc[-1]

    r = returns.dropna().to_numpy()  # 4 daily returns (day0 has none)
    excess = r - 0.0001
    expected_sharpe = excess.mean() / excess.std(ddof=1) * np.sqrt(252)
    downside = np.minimum(excess, 0.0)
    expected_sortino = excess.mean() / np.sqrt((downside**2).mean()) * np.sqrt(252)
    expected_vol = r.std(ddof=1) * np.sqrt(252)

    nav_arr = nav.to_numpy()
    cummax = np.maximum.accumulate(nav_arr)
    expected_mdd = (nav_arr / cummax - 1.0).min()
    n = len(nav_arr)
    expected_ann_return = (nav_arr[-1] / nav_arr[0]) ** (252 / n) - 1.0
    expected_calmar = expected_ann_return / abs(expected_mdd)

    assert sharpe == pytest.approx(expected_sharpe)
    assert sortino == pytest.approx(expected_sortino)
    assert vol == pytest.approx(expected_vol)
    assert mdd == pytest.approx(expected_mdd)
    assert calmar == pytest.approx(expected_calmar)


def test_rolling_indicators_use_all_available_history_below_window() -> None:
    """docs/DATA_MODELS.md: "if history < 756d, use all available history"
    — a window bigger than the series must not produce all-NaN."""
    idx = pd.bdate_range("2021-01-01", periods=10)
    nav = pd.Series(100.0 + np.arange(10, dtype=float), index=idx)
    returns = ratios.daily_returns(nav)
    rf = pd.Series(0.0, index=idx)
    sharpe = ratios.rolling_sharpe(returns, rf, window=756)
    assert sharpe.iloc[2:].notna().all()


# -- cumulative_return ----------------------------------------------------


def test_cumulative_return_uses_nearest_previous_trading_day() -> None:
    idx = pd.bdate_range("2021-01-01", periods=100)
    nav = pd.Series(np.linspace(100.0, 200.0, 100), index=idx)
    as_of = idx[-1]

    got = ratios.cumulative_return(nav, as_of, 91)

    target = as_of - pd.Timedelta(days=91)
    expected_start_idx = idx[idx <= target][-1]
    expected = nav.loc[as_of] / nav.loc[expected_start_idx] - 1.0
    assert got == pytest.approx(expected)


def test_cumulative_return_none_when_insufficient_history() -> None:
    idx = pd.bdate_range("2021-01-01", periods=10)
    nav = pd.Series(100.0 + np.arange(10, dtype=float), index=idx)
    assert ratios.cumulative_return(nav, idx[-1], 365) is None


# -- weight normalization -------------------------------------------------


def test_normalize_weights_handles_percent_and_fraction_uniformly() -> None:
    percent = ratios._normalize_weights({"A": 60.0, "B": 40.0})
    fraction = ratios._normalize_weights({"A": 0.6, "B": 0.4})
    assert percent["A"] == pytest.approx(0.6)
    assert percent["B"] == pytest.approx(0.4)
    assert fraction == pytest.approx(percent)


# -- test_nav_conventions_golden (docs/TASKS.md Phase 8 named test) -------


def test_nav_conventions_golden() -> None:
    """One fixed 3-asset (A, B, cash) fixture, entirely within one month (no
    rebalance) so NAV has a closed-form reference; sharpe/sortino/calmar/
    max_drawdown/volatility are then checked over the WHOLE window via an
    independent numpy hand-calc — this is the named DoV test
    (docs/MILESTONES.md M4 "golden numbers"; docs/TASKS.md Phase 8
    `test_nav_conventions_golden`)."""
    idx = pd.bdate_range("2021-03-01", periods=10)  # all March 2021
    a_prices = [100.0, 101.0, 99.0, 102.0, 101.0, 103.0, 104.0, 102.0, 105.0, 106.0]
    b_prices = [50.0, 50.5, 50.2, 49.8, 50.0, 50.3, 50.1, 50.4, 50.6, 50.5]
    prices = {
        "A": pd.Series(a_prices, index=idx),
        "B": pd.Series(b_prices, index=idx),
    }
    rf = ratios.rf_daily(pd.Series([1.5] * 10, index=idx))
    weights = {"A": 0.5, "B": 0.3, "cash": 0.2}

    nav = ratios.synthesize_nav(weights, prices, rf)
    expected_nav = _closed_form_nav_no_rebalance(weights, prices, rf)
    pd.testing.assert_series_equal(nav, expected_nav, check_names=False)

    returns = ratios.daily_returns(nav)
    window = 10
    mdd_series = ratios.rolling_max_drawdown(nav, window)
    sharpe = ratios.rolling_sharpe(returns, rf, window).iloc[-1]
    sortino = ratios.rolling_sortino(returns, rf, window).iloc[-1]
    calmar = ratios.rolling_calmar(nav, mdd_series, window).iloc[-1]
    mdd = mdd_series.iloc[-1]
    vol = ratios.rolling_volatility(returns, window).iloc[-1]

    r = returns.dropna().to_numpy()
    rf_arr = rf.reindex(returns.index).to_numpy()[1:]
    excess = r - rf_arr
    expected_sharpe = excess.mean() / excess.std(ddof=1) * np.sqrt(252)
    downside = np.minimum(excess, 0.0)
    expected_sortino = excess.mean() / np.sqrt((downside**2).mean()) * np.sqrt(252)
    expected_vol = r.std(ddof=1) * np.sqrt(252)

    nav_arr = nav.to_numpy()
    cummax = np.maximum.accumulate(nav_arr)
    expected_mdd = (nav_arr / cummax - 1.0).min()
    n = len(nav_arr)
    expected_ann_return = (nav_arr[-1] / nav_arr[0]) ** (252 / n) - 1.0
    expected_calmar = expected_ann_return / abs(expected_mdd)

    assert sharpe == pytest.approx(expected_sharpe)
    assert sortino == pytest.approx(expected_sortino)
    assert vol == pytest.approx(expected_vol)
    assert mdd == pytest.approx(expected_mdd)
    assert calmar == pytest.approx(expected_calmar)
