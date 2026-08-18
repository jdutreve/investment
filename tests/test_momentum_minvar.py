"""Unit tests for AAAF-R's pure decision logic (mechanical/momentum_minvar.py):
momentum scoring, top-N selection, the Ledoit-Wolf + bounded min-variance
solve and its equal-weight fallback, and the change-point map `build_targets`
produces for `replay.shadow_book_nav`.

No DB here — the seed-level wiring (step 12's skip-if-missing contract) is
covered in test_db.py, alongside the other step-12 producers."""

from unittest import mock

import numpy as np
import pandas as pd
import pytest

from investment.mechanical import momentum_minvar as mmv
from investment.mechanical.replay import shadow_book_nav


def _prices(index: pd.DatetimeIndex, drift: float, seed: int, noise: float = 0.01) -> pd.Series:
    rng = np.random.default_rng(seed)
    steps = 1.0 + drift + rng.normal(0.0, noise, len(index))
    return pd.Series(100.0 * np.cumprod(steps), index=index)


def test_momentum_scores_needs_full_lookback() -> None:
    """A ticker with fewer than `lookback` prior sessions AS OF `t` is skipped
    rather than scored on a shorter window (recipe step 2's `t-180` is not
    well-defined otherwise)."""
    index = pd.bdate_range("2020-01-01", periods=200)
    prices = {
        "SPY": _prices(index, 0.0004, 1),
        "IWM": _prices(index[100:], 0.0004, 2),  # starts too late
    }
    scores = mmv.momentum_scores(prices, index[190])
    assert "SPY" in scores
    assert "IWM" not in scores


def test_momentum_scores_matches_the_recipe_formula() -> None:
    index = pd.bdate_range("2020-01-01", periods=200)
    prices = {"SPY": pd.Series(np.arange(1.0, 201.0), index=index)}
    t = index[190]
    scores = mmv.momentum_scores(prices, t, lookback=180)
    p_t = prices["SPY"].loc[t]
    p_lookback = prices["SPY"].iloc[190 - 180]
    assert scores["SPY"] == pytest.approx(p_t / p_lookback - 1.0)


def test_select_top_picks_highest_momentum_no_filter() -> None:
    """No trend filter, no cash pass — the recipe's own step 3."""
    scores = {"A": 0.10, "B": -0.05, "C": 0.20, "D": 0.01, "E": 0.15, "F": 0.30}
    assert mmv.select_top(scores, n=5) == ["F", "C", "E", "A", "D"]


def _synthetic_returns(
    tickers: list[str], periods: int = mmv.COV_LOOKBACK_SESSIONS
) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=periods + 1)
    return pd.DataFrame(
        {t: _prices(index, 0.0002, seed).pct_change().dropna() for seed, t in enumerate(tickers)}
    )


def test_min_variance_weights_sum_to_one_and_respect_bounds() -> None:
    tickers = ["A", "B", "C", "D", "E"]
    weights = mmv.min_variance_weights(_synthetic_returns(tickers))
    assert set(weights) == set(tickers)
    assert sum(weights.values()) == pytest.approx(1.0)
    for w in weights.values():
        assert mmv.WEIGHT_MIN - 1e-9 <= w <= mmv.WEIGHT_MAX + 1e-9


def test_min_variance_downweights_the_high_volatility_asset() -> None:
    """A REGRESSION TEST for a real bug caught verifying this module
    end-to-end: with the RAW (unscaled) `w @ cov @ w` objective, SLSQP's
    default finite-difference gradient step is too coarse for daily-return
    covariance magnitudes (~1e-4 to 1e-8) — it read the slope as noise,
    declared `success=True` at the untouched equal-weight starting point, and
    every prior test here (drawing all tickers from the same drift/noise
    scale) was too symmetric to notice: equal weight was ALSO close to the
    true optimum there, so a frozen optimizer and a converged one looked
    identical. This fixture gives one ticker ~8x the daily volatility of the
    rest, where equal weight is clearly not optimal, so a stalled solve is
    visible: it would return exactly 0.2 for every asset, including the
    volatile one."""
    index = pd.bdate_range("2020-01-01", periods=64)
    returns = pd.DataFrame(
        {
            "LOW_A": _prices(index, 0.0001, 1, noise=0.003).pct_change().dropna(),
            "LOW_B": _prices(index, 0.0001, 2, noise=0.003).pct_change().dropna(),
            "LOW_C": _prices(index, 0.0001, 3, noise=0.003).pct_change().dropna(),
            "LOW_D": _prices(index, 0.0001, 4, noise=0.003).pct_change().dropna(),
            "HIGH_VOL": _prices(index, 0.0001, 5, noise=0.025).pct_change().dropna(),
        }
    )
    weights = mmv.min_variance_weights(returns)
    assert weights["HIGH_VOL"] == pytest.approx(mmv.WEIGHT_MIN, abs=1e-6)
    assert weights["HIGH_VOL"] < min(weights[t] for t in ["LOW_A", "LOW_B", "LOW_C", "LOW_D"])


def test_min_variance_falls_back_to_equal_weight_on_optimizer_failure() -> None:
    """Recipe step 9: `not result.success` must fall back, never propagate a
    non-converged solve as if it were the answer."""
    tickers = ["A", "B", "C", "D", "E"]
    with mock.patch.object(mmv, "minimize") as minimize:
        minimize.return_value = mock.Mock(success=False, x=np.zeros(5), message="did not converge")
        weights = mmv.min_variance_weights(_synthetic_returns(tickers))
    assert weights == dict.fromkeys(tickers, 0.2)


def test_min_variance_falls_back_when_shrinkage_raises() -> None:
    """The other half of step 9: an exception anywhere in the solve (a
    singular/degenerate input) must not crash the monthly walk."""
    tickers = ["A", "B", "C", "D", "E"]
    with mock.patch.object(mmv, "LedoitWolf", side_effect=RuntimeError("degenerate")):
        weights = mmv.min_variance_weights(_synthetic_returns(tickers))
    assert weights == dict.fromkeys(tickers, 0.2)


def test_build_targets_emits_nothing_before_warmup() -> None:
    """Fewer sessions than `MOMENTUM_LOOKBACK_SESSIONS` exist anywhere in the
    series — no date can be scored, so none is emitted (not even an
    equal-weight placeholder — `market_signal.HISTORY_START`'s "ask for the
    beginning, let the data decide")."""
    index = pd.bdate_range("2020-01-01", periods=170)
    prices = {t: _prices(index, 0.0002, i) for i, t in enumerate(mmv.UNIVERSE)}
    dates = [index[100], index[150], index[169]]
    assert mmv.build_targets(dates, prices) == {}


def test_build_targets_emits_a_full_book_once_warmed_up() -> None:
    index = pd.bdate_range("2020-01-01", periods=260)
    prices = {t: _prices(index, 0.0002, i) for i, t in enumerate(mmv.UNIVERSE)}
    dates = [index[185], index[220], index[259]]  # all past the 180-session warm-up
    targets = mmv.build_targets(dates, prices)
    assert set(targets) == set(dates)
    for target in targets.values():
        assert len(target) == mmv.TOP_N
        assert sum(target.values()) == pytest.approx(100.0)
        for w in target.values():
            assert mmv.WEIGHT_MIN * 100 - 1e-6 <= w <= mmv.WEIGHT_MAX * 100 + 1e-6


def test_targets_round_trip_through_shadow_book_nav() -> None:
    """`build_targets`' change-point map is exactly what
    `replay.shadow_book_nav` consumes — the same engine the MS-stack and its
    control arm are priced on, so AAAF-R is measured on the same footing."""
    index = pd.bdate_range("2020-01-01", periods=280)
    prices = {t: _prices(index, 0.0003, i) for i, t in enumerate(mmv.UNIVERSE)}
    decision_prices = {t: p.shift(1) for t, p in prices.items()}
    dates = [index[185], index[220], index[259]]
    targets = mmv.build_targets(dates, decision_prices)
    rf = pd.Series(0.0001, index=index)

    nav, turnover = shadow_book_nav(targets, prices, rf, mmv.COST_BPS, index)

    assert not nav.empty
    assert (nav > 0).all()
    assert turnover > 0.0  # three re-solves, each charged ADR-010's 23bps
