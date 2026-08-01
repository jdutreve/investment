"""M6-bis unit tests for the market-signal stack's PURE decision logic
(docs/V1_STRATEGY.md, ADR-007) — `classify_regime`, `apply_trend_overlay`,
`build_targets` in `mechanical/market_signal.py`, no DB.

The full anti-drift reproduction of the 11.26%/-23.8% backtest is an integration
check on the live DB (scratchpad/validate_market_signal.py); these pin the classifier,
the overlay and the switch hysteresis at the edges the backtest exercised.
(9.85%/-24% was the pre-hysteresis pair — ADR-007 fourth addendum.)
"""

import pandas as pd

from investment.mechanical import market_signal
from investment.mechanical.market_signal import apply_trend_overlay, build_targets, classify_regime

# The book keys are deliberately verbose (ADR-007 third addendum: the Worker is
# an LLM that reads them as semantic context). Aliased here so assertions stay
# on one line and a typo in a 37-char literal cannot pass silently.
WIDE = "credit-spread-wide"
TIGHT_FLAT = "credit-spread-tight-yield-curve-flat"
TIGHT_STEEP = "credit-spread-tight-yield-curve-steep"


def test_wide_spread_is_wide_credit() -> None:
    # spread above its 10y median -> WIDE, whatever the slope says.
    assert classify_regime(spread=2.5, spread_median=1.8, slope=-0.5, slope_median=1.0) == WIDE


def test_tight_spread_flat_slope_is_tight_flat() -> None:
    # spread below median, slope below its median (flat/inverted) -> TIGHT_FLAT.
    assert classify_regime(spread=1.2, spread_median=1.8, slope=0.3, slope_median=1.0) == TIGHT_FLAT


def test_tight_spread_steep_slope_is_tight_steep() -> None:
    # spread below median, slope above its median (steep) -> TIGHT_STEEP.
    assert (
        classify_regime(spread=1.2, spread_median=1.8, slope=2.0, slope_median=1.0) == TIGHT_STEEP
    )


def test_missing_median_defaults_to_wide_credit() -> None:
    # Warm-up before 10y of history: NaN median -> the equity-tilted WIDE book.
    assert (
        classify_regime(spread=1.2, spread_median=float("nan"), slope=0.3, slope_median=1.0) == WIDE
    )


def test_overlay_redirects_below_trend_sleeve_to_haven() -> None:
    # WIDE book SPY50/IWN40/GLD10 with SPY below its 200d MA -> SPY's 50 to IEF.
    out = apply_trend_overlay(market_signal.BOOKS[WIDE], frozenset({"SPY"}))
    assert out == {"IEF": 50.0, "IWN": 40.0, "GLD": 10.0}


def test_overlay_merges_both_sleeves_into_haven() -> None:
    # TIGHT_FLAT book SPY50/GLD40/IWN10 with BOTH below trend -> IEF piles to 90.
    # (This is the >50 concentration the cap confrontation flags — pinned here so
    # a future change to the overlay cannot silently alter it.)
    out = apply_trend_overlay(market_signal.BOOKS[TIGHT_FLAT], frozenset({"SPY", "GLD"}))
    assert out == {"IEF": 90.0, "IWN": 10.0}


def test_overlay_noop_when_above_trend() -> None:
    assert apply_trend_overlay(market_signal.BOOKS[WIDE], frozenset()) == market_signal.BOOKS[WIDE]


def test_trend_haven_is_exempt_from_single_asset_cap() -> None:
    # ADR-007 addendum choice (a): the overlay's flight to safety can pile 90%
    # into IEF; the single-asset cap does not bind that HAVEN concentration.
    from investment.mechanical.gates import Caps, concentration_ok

    book = apply_trend_overlay(market_signal.BOOKS[TIGHT_FLAT], frozenset({"SPY", "GLD"}))
    caps = Caps(max_single_asset_pct=50.0, max_drawdown_pct=-25.0)
    assert book == {"IEF": 90.0, "IWN": 10.0}
    assert not concentration_ok(book, caps)  # 90 breaches the cap unexempted
    assert concentration_ok(book, caps, exempt=frozenset({market_signal.TREND_HAVEN}))


def test_build_targets_emits_only_on_change() -> None:
    # Two decision dates in the same (credit-spread-wide) regime, above trend -> one target.
    idx = pd.to_datetime(["2020-01-06", "2020-02-03"])
    spread = pd.Series([2.5, 2.6], index=idx)  # both wide -> credit-spread-wide
    slope = pd.Series([1.0, 1.0], index=idx)
    spread_med = pd.Series([1.8, 1.8], index=idx)
    slope_med = pd.Series([1.0, 1.0], index=idx)
    # prices ABOVE their MA -> no trend redirect, book stays the plain credit-spread-wide book.
    mas = {t: pd.Series([1.0, 1.0], index=idx) for t in market_signal.TREND_SLEEVES}
    prices = {t: pd.Series([1000.0, 1000.0], index=idx) for t in ("SPY", "IWN", "GLD")}
    targets = build_targets(idx, spread, slope, spread_med, slope_med, mas, prices)
    assert list(targets) == [idx[0]]
    assert targets[idx[0]] == market_signal.BOOKS[WIDE]


def _steady_switch_frame(
    n_after: int,
) -> tuple[pd.DatetimeIndex, tuple[pd.Series, ...], dict, dict]:
    """One wide print, then `n_after` consecutive tight+steep prints."""
    idx = pd.to_datetime(["2020-01-06"] + [f"2020-{m:02d}-03" for m in range(2, 2 + n_after)])
    spread = pd.Series([2.5] + [1.2] * n_after, index=idx)  # wide, then tight
    slope = pd.Series([1.0] + [2.0] * n_after, index=idx)  # then steep
    spread_med = pd.Series([1.8] * len(idx), index=idx)
    slope_med = pd.Series([1.0] * len(idx), index=idx)
    # Prices ABOVE their MA: the overlay is a no-op, so these tests isolate the
    # hysteresis and assert against the plain books.
    mas = {t: pd.Series([1.0] * len(idx), index=idx) for t in market_signal.TREND_SLEEVES}
    prices = {
        t: pd.Series([1000.0] * len(idx), index=idx) for t in ("SPY", "IWN", "GLD", "VCIT", "IEF")
    }
    return idx, (spread, slope, spread_med, slope_med), mas, prices


def test_build_targets_switches_after_confirmation() -> None:
    # CONFIRM_DECISIONS consecutive prints of the NEW state commit the switch,
    # and not one decision earlier (measured 2026-08-01 — see the constant).
    n = market_signal.CONFIRM_DECISIONS
    idx, series, mas, prices = _steady_switch_frame(n)
    targets = build_targets(idx, *series, mas, prices)
    assert list(targets) == [idx[0], idx[n]]
    assert targets[idx[n]] == market_signal.BOOKS[TIGHT_STEEP]


def test_build_targets_holds_through_an_unconfirmed_signal() -> None:
    # One decision short of confirmation: the stack must still hold the wide book.
    n = market_signal.CONFIRM_DECISIONS
    idx, series, mas, prices = _steady_switch_frame(n - 1)
    targets = build_targets(idx, *series, mas, prices)
    assert list(targets) == [idx[0]]
    assert targets[idx[0]] == market_signal.BOOKS[WIDE]


def test_build_targets_resets_the_count_when_the_candidate_flickers() -> None:
    # tight/steep, back to wide, tight/steep again: the streak restarts, so a
    # flickering signal never accumulates its way into a switch.
    idx = pd.to_datetime(["2020-01-06", "2020-02-03", "2020-03-02", "2020-04-06", "2020-05-04"])
    spread = pd.Series([2.5, 1.2, 2.5, 1.2, 1.2], index=idx)
    slope = pd.Series([1.0, 2.0, 1.0, 2.0, 2.0], index=idx)
    spread_med = pd.Series([1.8] * 5, index=idx)
    slope_med = pd.Series([1.0] * 5, index=idx)
    mas = {t: pd.Series([1000.0] * 5, index=idx) for t in market_signal.TREND_SLEEVES}
    prices = {t: pd.Series([1.0] * 5, index=idx) for t in ("SPY", "IWN", "GLD", "VCIT", "IEF")}
    targets = build_targets(idx, spread, slope, spread_med, slope_med, mas, prices)
    assert list(targets) == [idx[0]]  # only 2 consecutive at the end, short of 3


def test_trend_overlay_is_not_damped_by_the_hysteresis() -> None:
    # The book waits for confirmation; the 200d drawdown control must not. SPY
    # drops below its MA on the second decision while the regime is unchanged.
    idx = pd.to_datetime(["2020-01-06", "2020-02-03"])
    spread = pd.Series([2.5, 2.5], index=idx)  # wide throughout -> no regime change
    slope = pd.Series([1.0, 1.0], index=idx)
    spread_med = pd.Series([1.8, 1.8], index=idx)
    slope_med = pd.Series([1.0, 1.0], index=idx)
    mas = {"SPY": pd.Series([1.0, 1000.0], index=idx), "GLD": pd.Series([1.0, 1.0], index=idx)}
    prices = {t: pd.Series([1.0, 1.0], index=idx) for t in ("SPY", "IWN", "GLD", "IEF")}
    targets = build_targets(idx, spread, slope, spread_med, slope_med, mas, prices)
    assert list(targets) == [idx[0], idx[1]]
    assert targets[idx[1]]["IEF"] == market_signal.BOOKS[WIDE]["SPY"]  # SPY sleeve redirected
