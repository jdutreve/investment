"""M6-bis unit tests for the Verdad market-signal stack's PURE decision logic
(docs/V1_STRATEGY.md, ADR-007) — `classify_regime`, `apply_trend_overlay`,
`build_targets` in `mechanical/verdad.py`, no DB.

The full anti-drift reproduction of the 9.85%/-24% backtest is an integration
check on the live DB (scratchpad/validate_verdad.py); these pin the classifier
and the overlay at the edges the backtest exercised.
"""

import pandas as pd

from investment.mechanical import verdad
from investment.mechanical.verdad import apply_trend_overlay, build_targets, classify_regime


def test_wide_spread_is_growth() -> None:
    # spread above its 10y median -> growth, whatever the slope says.
    assert classify_regime(spread=2.5, spread_median=1.8, slope=-0.5, slope_median=1.0) == "growth"


def test_tight_spread_flat_slope_is_inflation() -> None:
    # spread below median, slope below its median (flat/inverted) -> inflation.
    assert (
        classify_regime(spread=1.2, spread_median=1.8, slope=0.3, slope_median=1.0) == "inflation"
    )


def test_tight_spread_steep_slope_is_slowdown() -> None:
    # spread below median, slope above its median (steep) -> slowdown.
    assert classify_regime(spread=1.2, spread_median=1.8, slope=2.0, slope_median=1.0) == "slowdown"


def test_missing_median_defaults_to_growth() -> None:
    # Warm-up before 10y of history: NaN median -> the equity-tilted growth book.
    assert (
        classify_regime(spread=1.2, spread_median=float("nan"), slope=0.3, slope_median=1.0)
        == "growth"
    )


def test_overlay_redirects_below_trend_sleeve_to_haven() -> None:
    # growth book SPY50/IWN40/GLD10 with SPY below its 200d MA -> SPY's 50 to IEF.
    out = apply_trend_overlay(verdad.BOOKS["growth"], frozenset({"SPY"}))
    assert out == {"IEF": 50.0, "IWN": 40.0, "GLD": 10.0}


def test_overlay_merges_both_sleeves_into_haven() -> None:
    # inflation book SPY50/GLD40/IWN10 with BOTH below trend -> IEF piles to 90.
    # (This is the >50 concentration the cap confrontation flags — pinned here so
    # a future change to the overlay cannot silently alter it.)
    out = apply_trend_overlay(verdad.BOOKS["inflation"], frozenset({"SPY", "GLD"}))
    assert out == {"IEF": 90.0, "IWN": 10.0}


def test_overlay_noop_when_above_trend() -> None:
    assert apply_trend_overlay(verdad.BOOKS["growth"], frozenset()) == verdad.BOOKS["growth"]


def test_trend_haven_is_exempt_from_single_asset_cap() -> None:
    # ADR-007 addendum choice (a): the overlay's flight to safety can pile 90%
    # into IEF; the single-asset cap does not bind that HAVEN concentration.
    from investment.mechanical.gates import Caps, concentration_ok

    book = apply_trend_overlay(verdad.BOOKS["inflation"], frozenset({"SPY", "GLD"}))
    caps = Caps(max_single_asset_pct=50.0, max_drawdown_pct=-25.0)
    assert book == {"IEF": 90.0, "IWN": 10.0}
    assert not concentration_ok(book, caps)  # 90 breaches the cap unexempted
    assert concentration_ok(book, caps, exempt=frozenset({verdad.TREND_HAVEN}))


def test_build_targets_emits_only_on_change() -> None:
    # Two decision dates in the same (growth) regime, above trend -> one target.
    idx = pd.to_datetime(["2020-01-06", "2020-02-03"])
    spread = pd.Series([2.5, 2.6], index=idx)  # both wide -> growth
    slope = pd.Series([1.0, 1.0], index=idx)
    spread_med = pd.Series([1.8, 1.8], index=idx)
    slope_med = pd.Series([1.0, 1.0], index=idx)
    # prices ABOVE their MA -> no trend redirect, book stays the plain growth book.
    mas = {t: pd.Series([1.0, 1.0], index=idx) for t in verdad.TREND_SLEEVES}
    prices = {t: pd.Series([1000.0, 1000.0], index=idx) for t in ("SPY", "IWN", "GLD")}
    targets = build_targets(idx, spread, slope, spread_med, slope_med, mas, prices)
    assert list(targets) == [idx[0]]
    assert targets[idx[0]] == verdad.BOOKS["growth"]


def test_build_targets_switches_on_regime_change() -> None:
    idx = pd.to_datetime(["2020-01-06", "2020-02-03"])
    spread = pd.Series([2.5, 1.2], index=idx)  # wide -> growth, then tight
    slope = pd.Series([1.0, 2.0], index=idx)  # then steep -> slowdown
    spread_med = pd.Series([1.8, 1.8], index=idx)
    slope_med = pd.Series([1.0, 1.0], index=idx)
    mas = {t: pd.Series([1000.0, 1000.0], index=idx) for t in verdad.TREND_SLEEVES}
    prices = {t: pd.Series([1.0, 1.0], index=idx) for t in ("SPY", "IWN", "GLD", "VCIT", "IEF")}
    targets = build_targets(idx, spread, slope, spread_med, slope_med, mas, prices)
    assert list(targets) == [idx[0], idx[1]]
    assert targets[idx[1]] == verdad.BOOKS["slowdown"]
