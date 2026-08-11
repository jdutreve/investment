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
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, None) == WIDE
    # Wide and calm -> still the risk-on book, exactly as before the knobs.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, 0.05) == WIDE
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
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, 0.05) == WIDE
    # Wide and TIGHTENING -> equally the risk-on book: the veto is one-sided.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, -0.50) == WIDE
    # A tight level is untouched by the veto — it only ever defers a WIDE read.
    assert market_signal.classify_regime(1.0, 2.0, 0.5, 1.0, 0.30) == TIGHT_FLAT
    # Warm-up: no speed yet, so no veto and no crash.
    assert market_signal.classify_regime(3.0, 2.0, 0.5, 1.0, None) == WIDE


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
    assert market_signal.classify_regime(1.0, 2.0, 0.5, 1.0, 0.30) == WIDE
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
        moving_averages={
            t: pd.Series([100.0], index=idx)
            for t in (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)
        },
        prices={
            t: pd.Series([200.0], index=idx)
            for t in (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)
        },
        spread_speed=pd.Series([widening], index=idx),
    )

    monkeypatch.setattr(market_signal, "SPREAD_STRESS_SLEEVE_GATE", None)
    monkeypatch.setattr(market_signal, "SPREAD_SPEED_VETO", None)
    off = market_signal.walk_decisions(**frame)[0]
    assert off.target == market_signal.BOOKS[WIDE]  # price says hold everything

    # The gate alone, with the veto held off: the two are separable hypotheses
    # and this test is about the sleeves, not about which book was selected.
    monkeypatch.setattr(market_signal, "SPREAD_STRESS_SLEEVE_GATE", 0.20)
    on = market_signal.walk_decisions(**frame)[0]
    assert on.target == {"IEF": 90.0, "GLD": 10.0}  # SPY+IWN emptied, GLD kept
    assert all(on.trend[t].credit_gated for t in market_signal.EQUITY_SLEEVES)
    assert not on.trend["GLD"].credit_gated  # not equity, not gated
    # the audit trail explains itself: below, yet priced above its own average
    assert on.trend["SPY"].below and on.trend["SPY"].price > (on.trend["SPY"].moving_average or 0)


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
        moving_averages={
            t: pd.Series([100.0], index=idx)
            for t in (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)
        },
        prices={
            t: pd.Series([200.0], index=idx)
            for t in (*market_signal.TREND_SLEEVES, market_signal.TREND_HAVEN)
        },
        spread_speed=pd.Series([-0.10], index=idx),  # wide, but tightening
    )[0]
    assert decision.target == market_signal.BOOKS[WIDE]


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
    rendered "10-year" and 200 stays "200-day", so a trading-year division is
    accepted alongside the raw number."""
    if isinstance(value, list | tuple):
        return all(str(v) in text for v in value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        candidates = {f"{value:g}", str(value)}
        if value >= 252:
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
        moving_averages={t: pd.Series([100.0], index=idx) for t in tickers},
        prices={t: pd.Series([200.0], index=idx) for t in tickers},  # all ABOVE their 200d
        spread_speed=pd.Series([1.43], index=idx),  # widening hard
    )[0]

    # tight-steep is VCIT 50 / IEF 40 / IWN 10; the gate empties IWN and VCIT.
    assert decision.target == {"IEF": 100.0}
    assert decision.trend["VCIT"].credit_gated  # it explains itself
    assert decision.trend["VCIT"].price > (decision.trend["VCIT"].moving_average or 0)

    # ...and with the gate off (veto still on, so the book is the same), VCIT is
    # held at book weight: it is NOT trend-checked, only stress-gated.
    monkeypatch.setattr(market_signal, "SPREAD_STRESS_SLEEVE_GATE", None)
    ungated = market_signal.walk_decisions(
        dates=idx,
        spread=pd.Series([5.5], index=idx),
        slope=pd.Series([2.0], index=idx),
        spread_median=pd.Series([2.3], index=idx),
        slope_median=pd.Series([1.0], index=idx),
        moving_averages={t: pd.Series([100.0], index=idx) for t in tickers},
        prices={t: pd.Series([200.0], index=idx) for t in tickers},
        spread_speed=pd.Series([1.43], index=idx),
    )[0]
    assert ungated.target["VCIT"] == 50.0
