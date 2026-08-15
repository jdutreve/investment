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

from investment.db.seed_data import PORTFOLIOS
from investment.mechanical import market_signal
from investment.mechanical.gates import Caps
from investment.mechanical.market_signal import apply_trend_overlay, build_targets, classify_regime

# The book keys are deliberately verbose (ADR-007 third addendum: the Worker is
# an LLM that reads them as semantic context). Aliased here so assertions stay
# on one line and a typo in a 37-char literal cannot pass silently.
# The BINDING single-asset cap, read from the seed rather than typed. Every one
# of these was the literal `50.0` until 2026-08-14, when the owner raised the cap
# to 60 for the tight-flat book's SPY 60 — and four tests failed for asserting a
# number the system no longer used. A cap the tests hard-code is a cap they stop
# testing the day it moves.
_SEEDED_CAP = next(
    p["max_single_asset_pct"] for p in PORTFOLIOS if p["id"] == market_signal.STACK_PORTFOLIO_ID
)

# SPLIT ON 2026-08-13: `WIDE` named one book when the wide branch ignored the
# slope, and there are two now. The alias is gone rather than repointed — a name
# that silently means "one of the two wide books" is how the first version of
# this file would have kept passing while testing half the rule.
WIDE_FLAT = "credit-spread-wide-yield-curve-flat"
WIDE_STEEP = "credit-spread-wide-yield-curve-steep"
TIGHT_FLAT = "credit-spread-tight-yield-curve-flat"
TIGHT_STEEP = "credit-spread-tight-yield-curve-steep"


def _frames(one: dict[str, pd.Series]) -> dict[int, dict[str, pd.Series]]:
    """The same moving-average frame under EVERY `MA_WINDOWS` line.

    These fixtures set a sleeve unambiguously above or below its average, and
    the graduated overlay only splits a sleeve when its windows DISAGREE — so
    repeating one frame keeps every existing assertion about full redirects
    exactly as it was, while `test_a_sleeve_below_one_line_of_two_moves_half`
    covers the state the change actually adds."""
    return dict.fromkeys(market_signal.MA_WINDOWS, one)


def test_a_sleeve_below_one_line_of_two_moves_half() -> None:
    """THE STATE THE GRADUATED OVERLAY ADDS (2026-08-14). One line breached out
    of two moves half the sleeve; the rest is held. Before this the overlay was
    all-or-nothing on a single 300d line, which is what made it late AND violent
    — it waited for the slow average and then moved the whole weight at once."""
    assert len(market_signal.MA_WINDOWS) == 2  # the shares below assume it
    fast, slow = market_signal.MA_WINDOWS
    idx = pd.to_datetime(["2020-03-02"])
    tickers = (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)
    # SPY below its FAST line only; everything else comfortably above both.
    mas = {
        fast: {t: pd.Series([2000.0 if t == "SPY" else 100.0], index=idx) for t in tickers},
        slow: {t: pd.Series([100.0], index=idx) for t in tickers},
    }
    decision = market_signal.walk_decisions(
        dates=idx,
        spread=pd.Series([1.2], index=idx),  # tight
        slope=pd.Series([0.5], index=idx),  # flat
        spread_median=pd.Series([1.8], index=idx),
        slope_median=pd.Series([1.0], index=idx),
        moving_averages=mas,
        prices={t: pd.Series([1000.0], index=idx) for t in tickers},
        spread_speed=pd.Series([0.0], index=idx),
    )[0]

    assert decision.trend["SPY"].share == 0.5
    assert decision.trend["SPY"].below  # "out" is still true at a half share
    assert decision.trend["GLD"].share == 0.0
    # Half of tight-flat's SPY weight goes to IEF, the rest of the book is held.
    book = market_signal.BOOKS[TIGHT_FLAT]
    expected = {t: w for t, w in book.items() if t != "SPY"}
    expected["SPY"] = book["SPY"] / 2
    expected[market_signal.TREND_HAVEN] = book["SPY"] / 2
    assert decision.target == expected


def test_wide_spread_reads_the_slope_too() -> None:
    # Spread above its 10y median -> WIDE, and since the 2x2 the slope then
    # picks WHICH wide book. Before 2026-08-13 both of these returned one key.
    assert classify_regime(spread=2.5, spread_median=1.8, slope=-0.5, slope_median=1.0) == WIDE_FLAT
    assert classify_regime(spread=2.5, spread_median=1.8, slope=2.0, slope_median=1.0) == WIDE_STEEP


def test_tight_spread_flat_slope_is_tight_flat() -> None:
    # spread below median, slope below its median (flat/inverted) -> TIGHT_FLAT.
    assert classify_regime(spread=1.2, spread_median=1.8, slope=0.3, slope_median=1.0) == TIGHT_FLAT


def test_tight_spread_steep_slope_is_tight_steep() -> None:
    # spread below median, slope above its median (steep) -> TIGHT_STEEP.
    assert (
        classify_regime(spread=1.2, spread_median=1.8, slope=2.0, slope_median=1.0) == TIGHT_STEEP
    )


def test_missing_median_defaults_to_wide_credit() -> None:
    # Warm-up before 10y of history: NaN median -> the equity-tilted WIDE read,
    # and a NaN SLOPE median reads flat, so the warm-up book is wide-flat.
    assert (
        classify_regime(spread=1.2, spread_median=float("nan"), slope=0.3, slope_median=1.0)
        == WIDE_FLAT
    )
    assert (
        classify_regime(
            spread=1.2, spread_median=float("nan"), slope=0.3, slope_median=float("nan")
        )
        == WIDE_FLAT
    )


def test_overlay_redirects_below_trend_sleeve_to_haven() -> None:
    # wide-steep book SPY50/IWN40/GLD10 with SPY below its MA -> SPY's 50 to IEF.
    out = apply_trend_overlay(market_signal.BOOKS[WIDE_STEEP], {"SPY": 1.0})
    assert out == {"IEF": 50.0, "IWN": 40.0, "GLD": 10.0}


def test_overlay_merges_both_sleeves_into_haven() -> None:
    """Both risky sleeves fully out -> the haven piles up past the single-asset
    cap, which is legal only because the haven chain is exempt. Derived from the
    book rather than typed: this asserted `IEF 90 / IWN 10` against a three-sleeve
    tight-flat, and said nothing at all once that book became SPY 60 / GLD 40."""
    book = market_signal.BOOKS[TIGHT_FLAT]
    out = apply_trend_overlay(book, dict.fromkeys(book, 1.0))
    assert out == {market_signal.TREND_HAVEN: sum(book.values())}
    assert out[market_signal.TREND_HAVEN] > _SEEDED_CAP


def test_overlay_noop_when_above_trend() -> None:
    for book in market_signal.BOOKS.values():
        assert apply_trend_overlay(book, {}) == book


def test_trend_haven_is_exempt_from_single_asset_cap() -> None:
    # ADR-007 addendum choice (a): the overlay's flight to safety piles the whole
    # book into IEF; the single-asset cap does not bind that HAVEN concentration.
    from investment.mechanical.gates import Caps, concentration_ok

    held = market_signal.BOOKS[TIGHT_FLAT]
    book = apply_trend_overlay(held, dict.fromkeys(held, 1.0))
    caps = Caps(max_single_asset_pct=_SEEDED_CAP, max_drawdown_pct=-25.0)
    assert book == {market_signal.TREND_HAVEN: 100.0}
    assert not concentration_ok(book, caps)  # the pile breaches the cap unexempted
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

    all_below = dict.fromkeys(("SPY", "GLD", "IWN", market_signal.TREND_HAVEN), 1.0)
    book = apply_trend_overlay(market_signal.BOOKS[TIGHT_FLAT], all_below)
    caps = Caps(max_single_asset_pct=_SEEDED_CAP, max_drawdown_pct=-25.0)

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
    mas = _frames({t: pd.Series([1.0, 1.0], index=idx) for t in market_signal.TREND_SLEEVES})
    prices = {t: pd.Series([1000.0, 1000.0], index=idx) for t in ("SPY", "IWN", "GLD")}
    targets = build_targets(idx, spread, slope, spread_med, slope_med, mas, prices)
    assert list(targets) == [idx[0]]
    # slope 1.0 == its median -> STEEP, so the wide-steep book (the one the 2x2
    # left at the Verdad allocation).
    assert targets[idx[0]] == market_signal.BOOKS[WIDE_STEEP]


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
    mas = _frames({t: pd.Series([1.0] * len(idx), index=idx) for t in market_signal.TREND_SLEEVES})
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
    # One decision short of confirmation: the stack must still hold the wide
    # book — wide-STEEP here, since `_steady_switch_frame` opens with the slope
    # at its own median and `slope >= median` reads steep.
    n = market_signal.CONFIRM_DECISIONS
    idx, series, mas, prices = _steady_switch_frame(n - 1)
    targets = build_targets(idx, *series, mas, prices)
    assert list(targets) == [idx[0]]
    assert targets[idx[0]] == market_signal.BOOKS[WIDE_STEEP]


def test_build_targets_resets_the_count_when_the_candidate_flickers() -> None:
    # tight/steep, back to wide, tight/steep again: the streak restarts, so a
    # flickering signal never accumulates its way into a switch.
    idx = pd.to_datetime(["2020-01-06", "2020-02-03", "2020-03-02", "2020-04-06", "2020-05-04"])
    spread = pd.Series([2.5, 1.2, 2.5, 1.2, 1.2], index=idx)
    slope = pd.Series([1.0, 2.0, 1.0, 2.0, 2.0], index=idx)
    spread_med = pd.Series([1.8] * 5, index=idx)
    slope_med = pd.Series([1.0] * 5, index=idx)
    mas = _frames({t: pd.Series([1000.0] * 5, index=idx) for t in market_signal.TREND_SLEEVES})
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
    mas = _frames(
        {"SPY": pd.Series([1.0, 1000.0], index=idx), "GLD": pd.Series([1.0, 1.0], index=idx)}
    )
    prices = {t: pd.Series([1.0, 1.0], index=idx) for t in ("SPY", "IWN", "GLD", "IEF")}
    targets = build_targets(idx, spread, slope, spread_med, slope_med, mas, prices)
    assert list(targets) == [idx[0], idx[1]]
    assert targets[idx[1]]["IEF"] == market_signal.BOOKS[WIDE_STEEP]["SPY"]  # SPY sleeve redirected


def test_cap_violations_is_empty_over_a_run_and_names_what_breaks() -> None:
    """M6-bis's DoV in one assertion: the caps are clean over a whole run.

    This is the BUILD-TIME confrontation, distinct from the live gate — it walks
    every target the run held, and its drawdown leg is the whole-window figure
    (live, the rule is a 36-month rolling alert that never blocks — ADR-009)."""
    caps = Caps(max_single_asset_pct=_SEEDED_CAP, max_drawdown_pct=-25.0)
    idx = pd.to_datetime(["2020-01-06", "2020-02-03"])
    # A risk-off run: both sleeves below trend, so IEF piles to 90 — the
    # concentration the trend-haven exemption exists for.
    run = market_signal.MarketSignalRun(
        nav=pd.Series([100.0, 90.0], index=idx),
        targets={idx[0]: {"IEF": 90.0, "IWN": 10.0}, idx[1]: dict(market_signal.BOOKS[WIDE_STEEP])},
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
    book = market_signal.BOOKS[WIDE_STEEP]  # SPY 50 / IWN 40 / GLD 10

    # haven healthy: the classic redirect, everything lands in IEF
    healthy = market_signal.apply_trend_overlay(book, dict.fromkeys(("SPY", "IWN", "GLD"), 1.0))
    assert healthy == {market_signal.TREND_HAVEN: 100.0}

    # haven below trend too: cash instead, which cannot fall
    below = dict.fromkeys(("SPY", "IWN", "GLD", market_signal.TREND_HAVEN), 1.0)
    stressed = market_signal.apply_trend_overlay(book, below)
    assert stressed == {market_signal.TREND_FALLBACK_HAVEN: 100.0}


def test_a_book_holding_the_haven_moves_it_too_when_it_fails_the_test() -> None:
    """The steep book holds IEF 40 as a sleeve. Redirecting INTO the haven while
    leaving an existing haven sleeve untouched would judge one asset two ways in
    the same decision."""
    # VCIT 50 / IEF 40 / IWN 10
    steep = market_signal.BOOKS[TIGHT_STEEP]
    out = market_signal.apply_trend_overlay(steep, {"IWN": 1.0, market_signal.TREND_HAVEN: 1.0})
    assert out[market_signal.TREND_FALLBACK_HAVEN] == pytest.approx(50.0)  # IEF 40 + IWN 10
    assert out["VCIT"] == pytest.approx(50.0)


def test_every_risky_sleeve_is_trend_checked() -> None:
    """IWN was 40% of the wide book and outside the overlay — a
    rule premised on impaired credit holding maximum exposure to the most
    credit-sensitive equity sleeve there is. Found in both M8b runs, five times."""
    assert set(market_signal.TREND_SLEEVES) >= {"SPY", "GLD", "IWN"}
    for holdings in market_signal.BOOKS.values():
        risky = set(holdings) - {market_signal.TREND_HAVEN, "VCIT"}
        assert risky <= set(market_signal.TREND_SLEEVES), f"unchecked risky sleeve in {holdings}"


def test_vcit_is_under_the_overlay_against_the_measurement() -> None:
    """REVERSED 2026-08-14 by an owner conviction call, and this test records
    that it goes AGAINST the measurement rather than pretending otherwise.

    This file asserted `"VCIT" not in TREND_SLEEVES` and cited a clean 35-year
    rejection for it. That rejection still stands: adding VCIT rejects on the
    full sample and on 2009-2026 (Sortino 1.342 -> 1.330, CAGR 11.66% -> 11.58%,
    drawdown unchanged) and is marginally positive only on 1991-2008.

    What the rejection cannot see is why the owner overrode it. VCIT is 50% of
    the tight-steep book — 90% fixed income, in force today — and that book was
    held 61 times across 8 episodes of which SEVEN had falling rates. Frozen to
    it through a 2022, the rule loses 8.85% with VCIT unguarded and 4.66% with
    it checked, and at the trough the unguarded book sits at VCIT 50 / cash 50.
    A backtest cannot price insurance against a loss its sample does not hold.

    So the membership rule is no longer "can it fall far enough that avoiding it
    beats the whipsaw" — that question was measured and answered NO for VCIT.
    It is now that, OR the sleeve carries a risk the sample cannot speak to.
    The second clause is a judgement, it is the owner's, and it should be argued
    again before it is extended to a third sleeve."""
    assert "VCIT" in market_signal.TREND_SLEEVES
    # ...and it is trend-checked WITHOUT becoming stress-gated: the credit gate
    # was measured separately and rejected on every window (2026-08-11), and one
    # override does not carry the other.
    assert "VCIT" not in market_signal.STRESS_GATED_SLEEVES


def test_the_stack_calendar_is_where_every_sleeve_is_priced() -> None:
    """Step 6 item 1: the stack walks its OWN clock, not the bridge defender's
    NAV index (`market_signal.stack_calendar`). The rule is the same one
    `ratios.synthesize_nav` applies — a book cannot be priced on a day one of
    its sleeves has no price — and the consequence is the record opening
    1993-11-01, where VCIT's proxy begins.

    A day one sleeve MISSES is excluded even when it sits mid-history: the
    calendar is where the whole book is valuable, not where most of it is."""
    index = pd.bdate_range("1993-11-01", periods=200)
    full = pd.Series(100.0, index=index)
    prices = {t: full.copy() for t in market_signal.STACK_TICKERS}
    prices["VCIT"] = full[50:]  # the sleeve that starts latest, as in reality
    prices["IWN"] = full.drop(index[120])  # and one with a hole mid-history
    prices["SHY"] = full.copy()  # a haven CANDIDATE, loaded but not a sleeve

    calendar = market_signal.stack_calendar(prices)

    assert calendar[0] == index[50]
    assert index[120] not in calendar
    assert len(calendar) == 200 - 50 - 1


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
    for window in market_signal.MA_WINDOWS:
        assert str(window) in text
    assert str(market_signal.CONFIRM_DECISIONS) in text
    assert f"{market_signal.MEDIAN_WINDOW_YEARS}-year" in text
    # The books are the decision's whole output — all three, with their weights.
    for name, holdings in market_signal.BOOKS.items():
        assert name in text
        for ticker in holdings:
            assert ticker in text


def test_the_spread_trajectory_knobs_are_on_at_the_measured_value() -> None:
    """ON since 2026-08-11, at 0.20, by owner signature — the git gate ADR-006
    does not reach. Both earned it the same way: adopted on the full 35 years
    AND on each half, additive together, and worth +2.96pp of drawdown on
    1991-2008.

    0.20 is not a round number chosen for looking like one. Spread speed is
    points of BAA10Y per 30 days, and over 8722 days 0.20 is essentially the
    p90: the knobs bite on 8.7% of days and are idle otherwise, which is why
    the sweep degrades at 0.00 where the stack would sit in the lighter books
    permanently.

    The value is asserted here because a silent edit of it is a silent edit of
    the live allocation."""
    assert market_signal.SPREAD_SPEED_VETO == 0.20
    assert market_signal.SPREAD_STRESS_SLEEVE_GATE == 0.20
    # ...and the third mechanism of the same theme measured nil or unstable, so
    # it stays off — kept in the code so its rejection is a command, not a
    # rewrite.
    assert market_signal.SPREAD_SPEED_WIDE_TRIGGER is None

    # A warm-up with no speed yet must never veto: the rule falls back to the
    # level read it has always used.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, None) == WIDE_FLAT
    # Wide and calm -> still the risk-on book, exactly as before the knobs.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, 0.05) == WIDE_FLAT
    # Wide and still widening fast -> deferred to the curve branch.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, 0.30) == TIGHT_FLAT


def test_the_spread_speed_veto_defers_the_risk_on_book_it_does_not_hasten_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE DIRECTION IS THE WHOLE CLAIM, and it is the opposite of what "veto"
    suggests at first reading.

    `credit-spread-wide` is the RISK-ON book (SPY 50 / IWN 40 / GLD 10): the
    countercyclical bet that stress is already priced. The Worker's objection,
    in six formulations across independent dates, is to taking that bet while
    the stress is still forming — "the spread is wide because a credit event is
    still forming" (2008-07-01). So a spread that is wide AND still widening
    fast must fall through to the curve branch and its lighter book, not rush
    into the distressed one.

    Getting this backwards would measure the reverse of the claim and report it
    under the claim's name."""
    monkeypatch.setattr(market_signal, "SPREAD_SPEED_VETO", 0.10)

    # Wide level, widening FAST -> deferred to the curve branch.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, 0.30) == TIGHT_FLAT
    # Wide level, stabilised -> the risk-on book, as the rule always did.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, 0.05) == WIDE_FLAT
    # Wide and TIGHTENING -> equally the risk-on book: the veto is one-sided.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, -0.50) == WIDE_FLAT
    # A tight level is untouched by the veto — it only ever defers a WIDE read.
    assert market_signal.classify_regime(1.0, 2.0, 0.5, 1.0, 0.30) == TIGHT_FLAT
    # Warm-up: no speed yet, so no veto and no crash.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, None) == WIDE_FLAT


def test_the_wide_trigger_enters_on_speed_and_is_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The velocity theme's OTHER mechanism (2026-08-11), and the mirror image
    of the veto: the veto DEFERS the risk-on book while the spread widens, this
    ENTERS it on speed alone whatever the level says. Both came from the same
    six wordings; only measurement separates them, and it killed this one.

    Kept implemented and off, because "measured and rejected" is a verdict worth
    being able to reproduce — and the knob is what makes reproducing it a
    command rather than a rewrite."""
    assert market_signal.SPREAD_SPEED_WIDE_TRIGGER is None
    # Off: a tight level stays tight however fast the spread moves.
    assert market_signal.classify_regime(1.0, 2.0, 0.5, 1.0, 5.0) == TIGHT_FLAT

    monkeypatch.setattr(market_signal, "SPREAD_SPEED_WIDE_TRIGGER", 0.20)
    # Tight level, widening fast -> the risk-on book, entered on speed alone.
    assert market_signal.classify_regime(1.0, 2.0, 0.5, 1.0, 0.30) == WIDE_FLAT
    # Tight and calm -> untouched.
    assert market_signal.classify_regime(1.0, 2.0, 0.5, 1.0, 0.05) == TIGHT_FLAT
    # Warm-up with no speed yet never triggers.
    assert market_signal.classify_regime(1.0, 2.0, 0.5, 1.0, None) == TIGHT_FLAT


def test_the_sleeve_gate_empties_equity_on_credit_stress_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mechanism (c), asked for by four separate critiques: the book is SELECTED
    because credit is impaired and then holds 90% equities, with nothing but
    each sleeve's own price trend between the stack and that bet.

    Two properties, and the second is the one that rots quietly. The gate must
    empty the EQUITY sleeves only — GLD is trend-checked but is not equity — and
    a sleeve it redirects must SAY it was gated: `below=True` with a price above
    its moving average is a contradiction to anyone auditing the journal, which
    is the defect that had the digest announcing IEF while the target was
    cash."""
    idx = pd.to_datetime(["2020-03-02"])
    wide, widening = 3.0, 0.30
    frame = dict(
        dates=idx,
        spread=pd.Series([wide], index=idx),
        slope=pd.Series([1.0], index=idx),
        spread_median=pd.Series([2.0], index=idx),
        slope_median=pd.Series([1.0], index=idx),
        # every sleeve comfortably ABOVE its 200d, so nothing is redirected by price
        moving_averages=_frames(
            {
                t: pd.Series([100.0], index=idx)
                for t in (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)
            }
        ),
        prices={
            t: pd.Series([200.0], index=idx)
            for t in (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)
        },
        spread_speed=pd.Series([widening], index=idx),
    )

    monkeypatch.setattr(market_signal, "SPREAD_STRESS_SLEEVE_GATE", None)
    monkeypatch.setattr(market_signal, "SPREAD_SPEED_VETO", None)
    off = market_signal.walk_decisions(**frame)[0]
    assert off.target == market_signal.BOOKS[WIDE_STEEP]  # price says hold everything

    # The gate alone, with the veto held off: the two are separable hypotheses
    # and this test is about the sleeves, not about which book was selected.
    monkeypatch.setattr(market_signal, "SPREAD_STRESS_SLEEVE_GATE", 0.20)
    on = market_signal.walk_decisions(**frame)[0]
    assert on.target == {"IEF": 90.0, "GLD": 10.0}  # SPY+IWN emptied, GLD kept
    assert all(on.trend[t].credit_gated for t in market_signal.EQUITY_SLEEVES)
    assert not on.trend["GLD"].credit_gated  # not equity, not gated
    # the audit trail explains itself: below, yet priced above its own average
    # It is out with NO line breached — the gate, not the price. `breached` is
    # empty, which is exactly what a renderer needs to avoid printing
    # "price > moving_average" beside "below trend".
    assert on.trend["SPY"].below and on.trend["SPY"].breached == ()


def test_the_gate_is_idle_when_the_spread_is_wide_but_calm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BOTH conditions, as proposed: a wide LEVEL and a widening TRAJECTORY. A
    wide spread that has stopped moving is the state the countercyclical premise
    is built for — stress already priced — and gating there would fight ADR-007
    rather than refine it."""
    idx = pd.to_datetime(["2009-06-01"])
    monkeypatch.setattr(market_signal, "SPREAD_STRESS_SLEEVE_GATE", 0.20)
    monkeypatch.setattr(market_signal, "SPREAD_SPEED_VETO", None)
    decision = market_signal.walk_decisions(
        dates=idx,
        spread=pd.Series([3.0], index=idx),
        slope=pd.Series([1.0], index=idx),
        spread_median=pd.Series([2.0], index=idx),
        slope_median=pd.Series([1.0], index=idx),
        moving_averages=_frames(
            {
                t: pd.Series([100.0], index=idx)
                for t in (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)
            }
        ),
        prices={
            t: pd.Series([200.0], index=idx)
            for t in (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)
        },
        spread_speed=pd.Series([-0.10], index=idx),  # wide, but tightening
    )[0]
    assert decision.target == market_signal.BOOKS[WIDE_STEEP]


def test_describe_rule_states_every_active_knob() -> None:
    """FOURTH occurrence of one defect, and the third fix of this same function.

    `describe_rule` promises text that cannot go stale. On 2026-08-08 the
    promise was half kept — the sleeve list interpolated, the sentence around it
    hand-written — and the Worker spent an innovation re-proposing a cash
    fallback that already shipped. On 2026-08-11 it broke again, by my own hand:
    two knobs went live and this text did not mention them. Five replayed dates
    bought six innovations and FOUR re-proposed a running feature.

    Pairing the text with the knob registry is what makes the next one
    impossible: any knob that is ON must appear, with its value, in what the
    Worker reads. Knobs that are OFF must NOT — describing an option the rule
    does not use invites a proposal to switch on what is already off, which is
    the same waste from the other side."""
    from investment.mechanical.rule_revision import TESTABLE_PARAMETERS

    text = market_signal.describe_rule()
    for knob, attr in TESTABLE_PARAMETERS.items():
        value = getattr(market_signal, attr)
        if value is not None:
            assert _stated(value, text), (
                f"{knob} is ON at {value!r} and the rule text never says so"
            )

    # The off knob is absent, by name and by mechanism.
    assert market_signal.SPREAD_SPEED_WIDE_TRIGGER is None
    assert "whatever the level says" not in text


def test_describe_rule_states_the_caps_and_the_haven_exemption() -> None:
    """Raised THREE times across independent runs: "ms-stack carries
    max_single_asset_pct = 50, yet this month's overlay produces a 90% IEF
    sleeve". Every time it was a correct reading of a contradiction that only
    looked like one — the haven exemption lives in code the Worker cannot see,
    so three innovations were spent on a question one sentence answers.

    The caps are part of what DECIDED the month, so a rule text that omits them
    describes a rule the stack does not follow."""
    text = market_signal.describe_rule()

    assert "50%" in text
    for sleeve in market_signal.HAVEN_EXEMPT:
        assert sleeve in text, f"{sleeve} is cap-exempt and the rule text never says so"
    assert "legal by design" in text  # the reading it must prevent


def _stated(value: object, text: str) -> bool:
    """Is this knob's value discoverable in the prose?

    ARITHMETIC, not a hand-written map of knob -> expected phrasing: such a map
    would be one more list that names what exists today, which is the defect
    this whole test guards. A window of 2520 trading days is legitimately
    rendered "10-year" and 300 stays "300-day", so a trading-year division is
    accepted alongside the raw number.

    THE DIVISION MUST BE EXACT, and it was not — `value // 252` accepted the
    QUOTIENT of any window, so `MA_WINDOWS = (150, 300)` is satisfied by the digit
    "1" appearing anywhere in the prose, which it always does (the rule text is
    numbered "1."). The one knob whose staleness this test exists to catch was
    therefore unguarded on the very day the window moved, and the stale "200d"
    left in the sleeve-gate sentence passed it. A year-rendering is only a
    year-rendering when the window IS whole years."""
    if isinstance(value, list | tuple):
        return all(str(v) in text for v in value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        candidates = {f"{value:g}", str(value)}
        if value >= 252 and value % 252 == 0:
            candidates.add(str(int(value // 252)))
        return any(c in text for c in candidates)
    return str(value) in text


def test_the_stress_gate_reaches_sleeves_the_200d_does_not_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Worker's fourth wording, and the bug it exposed in the first fix.

    Its critique: the veto's escape route is the slope-decided tight-steep book,
    which holds VCIT 50 — investment-grade CREDIT — while the gate emptied the
    equities beside it and left the sleeve most exposed to the spread that
    triggered the gate.

    The first implementation rewrote entries of `trend`, which exists only for
    the 200d-checked set, so gating VCIT did NOTHING and measured as exactly
    zero on all three windows. Three identical zeros are not a market fact, and
    that implausibility is what exposed it.

    A gated sleeve outside the checked set must be redirected AND explain
    itself — and must not become trend-checked, since adding VCIT to the 200d
    overlay was measured and rejected on every window."""
    idx = pd.to_datetime(["2008-11-03"])
    # THE VETO MUST BE ON: it is what routes a wide-spread month to the
    # slope-decided book in the first place. With it off a wide spread selects
    # the wide book, which holds no VCIT — the first version of this test set it
    # to None and measured the wrong chain entirely.
    monkeypatch.setattr(market_signal, "SPREAD_SPEED_VETO", 0.20)
    monkeypatch.setattr(market_signal, "SPREAD_STRESS_SLEEVE_GATE", 0.20)
    monkeypatch.setattr(market_signal, "STRESS_GATED_SLEEVES", ("SPY", "IWN", "VCIT"))

    tickers = (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN, "VCIT")
    decision = market_signal.walk_decisions(
        dates=idx,
        spread=pd.Series([5.5], index=idx),  # wide
        slope=pd.Series([2.0], index=idx),  # steep -> the VCIT-holding book
        spread_median=pd.Series([2.3], index=idx),
        slope_median=pd.Series([1.0], index=idx),
        moving_averages=_frames({t: pd.Series([100.0], index=idx) for t in tickers}),
        prices={t: pd.Series([200.0], index=idx) for t in tickers},  # all ABOVE their 200d
        spread_speed=pd.Series([1.43], index=idx),  # widening hard
    )[0]

    # tight-steep is VCIT 50 / IEF 40 / IWN 10; the gate empties IWN and VCIT.
    assert decision.target == {"IEF": 100.0}
    assert decision.trend["VCIT"].credit_gated  # it explains itself
    assert decision.trend["VCIT"].breached == ()  # out by the gate, not by price

    # ...and with the gate off (veto still on, so the book is the same), VCIT is
    # held at book weight: it is NOT trend-checked, only stress-gated.
    monkeypatch.setattr(market_signal, "SPREAD_STRESS_SLEEVE_GATE", None)
    ungated = market_signal.walk_decisions(
        dates=idx,
        spread=pd.Series([5.5], index=idx),
        slope=pd.Series([2.0], index=idx),
        spread_median=pd.Series([2.3], index=idx),
        slope_median=pd.Series([1.0], index=idx),
        moving_averages=_frames({t: pd.Series([100.0], index=idx) for t in tickers}),
        prices={t: pd.Series([200.0], index=idx) for t in tickers},
        spread_speed=pd.Series([1.43], index=idx),
    )[0]
    assert ungated.target["VCIT"] == 50.0


# -- the control arm (2026-08-13) -------------------------------------------
#
# WHY THESE EXIST. The stack was only ever measured against a PASSIVE benchmark
# (All Weather, +3.80pp/yr — the number ADR-007 was signed on), never against
# the same trend overlay on a book that does not rotate. When that arm was
# finally built, the signal layer's whole marginal contribution turned out to be
# +0.24pp of CAGR and +0.20 of Sortino against the overlay's 30 points of
# drawdown (docs/V1_STRATEGY.md "The attribution"). These pin the control's
# defining property: it runs the overlay and NOTHING of the signal.


def _flat_frame(n: int) -> tuple[pd.DatetimeIndex, dict, dict]:
    """`n` monthly dates with every instrument well above its moving average."""
    idx = pd.to_datetime([f"2020-{m:02d}-03" for m in range(1, 1 + n)])
    tickers = ("SPY", "IWN", "GLD", "VCIT", "IEF")
    mas = {t: pd.Series([100.0] * n, index=idx) for t in tickers}
    prices = {t: pd.Series([200.0] * n, index=idx) for t in tickers}
    return idx, mas, prices


def test_the_control_arm_freezes_its_book_and_emits_only_on_change() -> None:
    """Above trend throughout: one target, and it IS the frozen book. The stack
    would have re-classified on every date; the control has nothing to classify
    with, which is the experiment."""
    idx, mas, prices = _flat_frame(3)
    targets = market_signal.trend_baseline_targets(idx, _frames(mas), prices)
    assert list(targets) == [idx[0]]
    assert targets[idx[0]] == market_signal.BOOKS[market_signal.TREND_BASELINE_BOOK]


def test_the_control_arm_takes_no_credit_input_at_all() -> None:
    """THE PROPERTY UNDER TEST, asserted on the signature rather than on an
    output: a control that could read the spread would not be a control. The
    stack's three signal entry points — `classify_regime`, `advance_hysteresis`
    and the stress gate — all need a spread or a slope, and none of them can be
    reached from a function that is never handed one."""
    import inspect

    accepted = set(inspect.signature(market_signal.trend_baseline_targets).parameters)
    assert accepted == {"dates", "moving_averages", "prices", "book"}


def test_the_control_arm_runs_the_same_overlay_and_the_same_haven_chain() -> None:
    """It must react to price exactly as the stack does — same redirect, same
    IEF-then-cash fallback — or the attribution would be measuring two different
    overlays and crediting the difference to the signal."""
    idx, mas, prices = _flat_frame(1)
    prices["SPY"] = pd.Series([50.0], index=idx)  # SPY alone below BOTH its lines
    targets = market_signal.trend_baseline_targets(idx, _frames(mas), prices)
    book = market_signal.BOOKS[market_signal.TREND_BASELINE_BOOK]
    assert targets[idx[0]][market_signal.TREND_HAVEN] == book["SPY"]

    # ...and when the haven is itself below trend, the destination is cash —
    # the 2026-08-07 symmetry fix, which the control inherits by construction
    # because it calls `apply_trend_overlay` rather than reimplementing it.
    for ticker in ("SPY", "GLD", "IWN", "IEF"):
        prices[ticker] = pd.Series([50.0], index=idx)
    fled = market_signal.trend_baseline_targets(idx, _frames(mas), prices)
    assert fled[idx[0]] == {market_signal.TREND_FALLBACK_HAVEN: 100.0}


def test_the_frozen_book_is_one_of_the_books_the_signal_chooses_between() -> None:
    """Measured, not picked: `credit-spread-tight-yield-curve-flat` is the book
    the signal itself holds 197 of 418 monthly decisions (47.1%). A control that
    froze a book the strategy never holds would answer a question nobody asked."""
    assert market_signal.TREND_BASELINE_BOOK in market_signal.BOOKS


def test_the_control_arm_is_seeded_and_declared_time_varying() -> None:
    """The id lives in two files that must agree — `market_signal` names it,
    `seed_data` seeds it — and nothing imports across, because seed_data cannot
    import market_signal without a cycle (replay already imports seed_data).
    So it is pinned here, the same technique `EQUITY_SLEEVES` uses against the
    ticker catalog.

    TIME_VARYING matters as much as the row: the book is frozen but the OVERLAY
    still rewrites it monthly, so `backfill_nav`'s constant weights would price
    a portfolio nobody holds — and membership is also what gives the control
    ADR-011's gate 0, so the Worker cannot retarget it."""
    from investment.db.seed_data import PORTFOLIOS, TIME_VARYING_PORTFOLIOS

    seeded = {p["id"]: p for p in PORTFOLIOS}
    assert market_signal.TREND_BASELINE_PORTFOLIO_ID in seeded
    assert market_signal.TREND_BASELINE_PORTFOLIO_ID in TIME_VARYING_PORTFOLIOS
    row = seeded[market_signal.TREND_BASELINE_PORTFOLIO_ID]
    assert row["enabled"] and not row["defender"]
    # The seeded allocation is the frozen book, or the ranking would describe
    # the control as holding something it never holds.
    assert row["allocation"] == {
        t: int(w) for t, w in market_signal.BOOKS[market_signal.TREND_BASELINE_BOOK].items()
    }


def test_a_half_out_haven_is_not_announced_as_cash() -> None:
    """THE OWNER-FACING BUG the graduated overlay introduced (found 2026-08-14).

    `writeback` built its sentence from `below_trend`, which is `share > 0`. Once
    a sleeve could be HALF out, a haven below one line of two was "below trend"
    and the digest announced "redirected to cash" — while `apply_trend_overlay`
    only abandons IEF at a FULL share, so the allocation printed on the next line
    was still in IEF. The text the owner reads to place the order contradicted
    the number beside it, which is the same defect as the 2026-08-08 line that
    said "redirected to IEF" while the target was cash.

    `Decision.haven` is now the single rule and both callers read it."""
    assert len(market_signal.MA_WINDOWS) == 2
    fast, slow = market_signal.MA_WINDOWS
    idx = pd.to_datetime(["2022-03-01"])
    tickers = (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)

    def decide(haven_below_fast: bool, haven_below_slow: bool) -> market_signal.Decision:
        mas = {
            fast: {
                t: pd.Series([2000.0 if (t == "IEF" and haven_below_fast) else 100.0], index=idx)
                for t in tickers
            },
            slow: {
                t: pd.Series([2000.0 if (t == "IEF" and haven_below_slow) else 100.0], index=idx)
                for t in tickers
            },
        }
        return market_signal.walk_decisions(
            dates=idx,
            spread=pd.Series([1.2], index=idx),
            slope=pd.Series([0.5], index=idx),
            spread_median=pd.Series([1.8], index=idx),
            slope_median=pd.Series([1.0], index=idx),
            moving_averages=mas,
            prices={t: pd.Series([1000.0], index=idx) for t in tickers},
            spread_speed=pd.Series([0.0], index=idx),
        )[0]

    half = decide(haven_below_fast=True, haven_below_slow=False)
    assert half.trend["IEF"].share == 0.5
    assert half.trend["IEF"].below  # it IS below trend...
    assert half.haven == market_signal.TREND_HAVEN  # ...and still the destination

    whole = decide(haven_below_fast=True, haven_below_slow=True)
    assert whole.trend["IEF"].share == 1.0
    assert whole.haven == market_signal.TREND_FALLBACK_HAVEN


def test_the_decisions_haven_is_the_one_the_overlay_actually_used() -> None:
    """Structural, not exemplary: whatever `Decision.haven` says must be a key of
    the target the same decision produced, on every reachable haven state. A
    sentence derived from one rule and an allocation from another is how the two
    disagreed in the first place."""
    book = market_signal.BOOKS[market_signal.TREND_BASELINE_BOOK]
    n = len(market_signal.MA_WINDOWS)
    for k in range(n + 1):
        shares = dict.fromkeys(book, 1.0) | {market_signal.TREND_HAVEN: k / n}
        target = market_signal.apply_trend_overlay(book, shares)
        haven = market_signal.TREND_FALLBACK_HAVEN if k / n >= 1.0 else market_signal.TREND_HAVEN
        assert haven in target, (k, target)
