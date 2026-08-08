"""M6-bis unit tests for the market-signal stack's PURE decision logic
(docs/V1_STRATEGY.md, ADR-007) — `classify_regime`, `apply_trend_overlay`,
`build_targets` in `mechanical/market_signal.py`, no DB.

The full anti-drift reproduction of the 11.26%/-23.8% backtest is an integration
check on the live DB (scratchpad/validate_market_signal.py); these pin the classifier,
the overlay and the switch hysteresis at the edges the backtest exercised.
(9.85%/-24% was the pre-hysteresis pair — ADR-007 fourth addendum.)
"""

import pandas as pd
import pytest

from investment.mechanical import market_signal
from investment.mechanical.gates import Caps
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


def test_the_cash_fallback_is_exempt_too_or_the_flight_to_safety_is_unreachable() -> None:
    """Owner, 2026-08-08, from the M8b run's four blocked 2022 dates.

    When the haven is ITSELF below trend the overlay sends everything to cash,
    and the exemption did not follow the destination when the fallback shipped
    the day before. On 2022-03-01, -05-02, -06-01 and -07-01 that produced a
    100%-cash target refused by the 50% cap, and the stack held its stale book
    through the drawdown — a refusal freezing the very move the overlay exists
    to make (the ADR-009 argument, transferred to the concentration leg)."""
    from investment.mechanical.gates import Caps, concentration_ok

    all_below = frozenset({"SPY", "GLD", "IWN", market_signal.TREND_HAVEN})
    book = apply_trend_overlay(market_signal.BOOKS[TIGHT_FLAT], all_below)
    caps = Caps(max_single_asset_pct=50.0, max_drawdown_pct=-25.0)

    assert book == {market_signal.TREND_FALLBACK_HAVEN: 100.0}
    assert not concentration_ok(book, caps)  # 100 breaches the cap unexempted
    # The IEF-only exemption is what shipped, and it is what blocked these dates.
    assert not concentration_ok(book, caps, exempt=frozenset({market_signal.TREND_HAVEN}))
    assert concentration_ok(book, caps, exempt=market_signal.HAVEN_EXEMPT)


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


def test_cap_violations_is_empty_over_a_run_and_names_what_breaks() -> None:
    """M6-bis's DoV in one assertion: the caps are clean over a whole run.

    This is the BUILD-TIME confrontation, distinct from the live gate — it walks
    every target the run held, and its drawdown leg is the whole-window figure
    (live, the rule is a 36-month rolling alert that never blocks — ADR-009)."""
    caps = Caps(max_single_asset_pct=50.0, max_drawdown_pct=-25.0)
    idx = pd.to_datetime(["2020-01-06", "2020-02-03"])
    # A risk-off run: both sleeves below trend, so IEF piles to 90 — the
    # concentration the trend-haven exemption exists for.
    run = market_signal.MarketSignalRun(
        nav=pd.Series([100.0, 90.0], index=idx),
        targets={idx[0]: {"IEF": 90.0, "IWN": 10.0}, idx[1]: dict(market_signal.BOOKS[WIDE])},
        turnover=1.0,
    )
    assert market_signal.cap_violations(run, caps, -0.238) == []

    # An UNMEASURED drawdown passes ("unmeasured is not bad"); a breaching one
    # is named, as is a non-haven sleeve over the cap.
    assert market_signal.cap_violations(run, caps, None) == []
    assert market_signal.cap_violations(run, caps, -0.40) == ["max_drawdown_pct@stack"]

    over = market_signal.MarketSignalRun(
        nav=pd.Series([100.0], index=idx[:1]),
        targets={idx[0]: {"SPY": 90.0, "IWN": 10.0}},
        turnover=0.0,
    )
    assert market_signal.cap_violations(over, caps, -0.10) == [
        f"max_single_asset_pct@{idx[0].date()}"
    ]


def test_the_overlay_checks_the_haven_it_redirects_into() -> None:
    """The Worker's sharpest M8b line, raised on 2022-02-01 in BOTH independent
    runs: "the overlay trends the sleeve it exits but not the sleeve it enters".
    A rule that flees a falling asset into a falling asset is not a drawdown
    control — in Feb 2022 it moved 40% into IEF while IEF was below its own 200d
    line, in the worst bond tape of the 35-year sample."""
    book = market_signal.BOOKS["credit-spread-wide"]  # SPY 50 / IWN 40 / GLD 10

    # haven healthy: the classic redirect, everything lands in IEF
    healthy = market_signal.apply_trend_overlay(book, frozenset({"SPY", "IWN", "GLD"}))
    assert healthy == {market_signal.TREND_HAVEN: 100.0}

    # haven below trend too: cash instead, which cannot fall
    below = frozenset({"SPY", "IWN", "GLD", market_signal.TREND_HAVEN})
    stressed = market_signal.apply_trend_overlay(book, below)
    assert stressed == {market_signal.TREND_FALLBACK_HAVEN: 100.0}


def test_a_book_holding_the_haven_moves_it_too_when_it_fails_the_test() -> None:
    """The steep book holds IEF 40 as a sleeve. Redirecting INTO the haven while
    leaving an existing haven sleeve untouched would judge one asset two ways in
    the same decision."""
    # VCIT 50 / IEF 40 / IWN 10
    steep = market_signal.BOOKS["credit-spread-tight-yield-curve-steep"]
    out = market_signal.apply_trend_overlay(steep, frozenset({"IWN", market_signal.TREND_HAVEN}))
    assert out[market_signal.TREND_FALLBACK_HAVEN] == pytest.approx(50.0)  # IEF 40 + IWN 10
    assert out["VCIT"] == pytest.approx(50.0)


def test_every_risky_sleeve_is_trend_checked() -> None:
    """IWN was 40% of the credit-spread-wide book and outside the overlay — a
    rule premised on impaired credit holding maximum exposure to the most
    credit-sensitive equity sleeve there is. Found in both M8b runs, five times."""
    assert set(market_signal.TREND_SLEEVES) >= {"SPY", "GLD", "IWN"}
    for holdings in market_signal.BOOKS.values():
        risky = set(holdings) - {market_signal.TREND_HAVEN, "VCIT"}
        assert risky <= set(market_signal.TREND_SLEEVES), f"unchecked risky sleeve in {holdings}"


def test_vcit_stays_outside_the_overlay_and_that_was_measured() -> None:
    """VCIT is 50% of the steep book and IS capable of falling — on 2026-08-08
    it sat below its own 200d line while real yields rose at accelerating speed,
    which looks exactly like the IWN gap fixed the same morning.

    It was measured rather than argued (`mechanical/rule_revision.py`, 35y):
    adding it moved CAGR 10.72% -> 10.65%, Sortino 1.17 -> 1.16, and the
    drawdown NOT AT ALL (-20.61% both ways), for more turnover. REJECTED on the
    acceptance test, both legs.

    The reason is the useful part. VCIT is investment-grade corporate credit:
    its drawdowns are shallow, so exiting below trend avoids a small loss AND
    misses the recovery, paying 23 bps each way. IWN is small-cap value with
    real crash beta, where the overlay catches something worth catching.

    So the membership rule is neither "asset class" nor "can it fall" — both
    were tried and both were wrong. It is CAN IT FALL FAR ENOUGH THAT AVOIDING
    IT BEATS THE WHIPSAW. Re-measure before adding a sleeve; do not reason it."""
    assert "VCIT" not in market_signal.TREND_SLEEVES


def test_describe_rule_states_every_knob_it_claims_to_generate() -> None:
    """`describe_rule` promises the Worker a description that cannot go stale.
    It went stale in six hours: the sleeve list interpolated, the sentence
    around it did not, and it kept saying below-trend sleeves go to IEF after
    the haven became trend-checked with a cash fallback. The Worker believed it
    and burned an innovation re-proposing the fallback that already shipped.

    So every constant the overlay turns on has to APPEAR in the text. This test
    fails the moment a knob is added to the mechanism without reaching the
    description — which is the only way the Worker learns about it."""
    text = market_signal.describe_rule()
    for sleeve in (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN):
        assert sleeve in text, f"{sleeve} is trend-checked but undescribed"
    assert market_signal.TREND_FALLBACK_HAVEN in text
    assert str(market_signal.MA_WINDOW_DAYS) in text
    assert str(market_signal.CONFIRM_DECISIONS) in text
    assert f"{market_signal.MEDIAN_WINDOW_DAYS // 252}-year" in text
    # The books are the decision's whole output — all three, with their weights.
    for name, holdings in market_signal.BOOKS.items():
        assert name in text
        for ticker in holdings:
            assert ticker in text
