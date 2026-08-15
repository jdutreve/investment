"""M6 unit tests for the shadow replay (docs/TASKS.md Task 9.1; docs/MILESTONES.md
M6 Definition of Verified) — the pure core of `mechanical/replay.py`, plus the
two tests Task 9.1 names explicitly.
"""

import dataclasses
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from investment.mechanical import ratios, replay
from investment.mechanical.gates import Caps, ProposalThresholds
from investment.mechanical.replay import (
    PortfolioMeta,
    RegimeInstance,
    ReplayInputs,
    ReplayThresholds,
    ScenarioMeta,
    shadow_book_nav,
)

THRESHOLDS = ReplayThresholds(
    proposal=ProposalThresholds(
        sortino_gap_min=0.02,
        calmar_min=1.5,
        min_allocation_change_pts=5.0,
        max_turnover_pct=30.0,
        blend_scenario_weight=0.4,
        blend_favors_weight=0.6,
    ),
    tiebreak_window=0.02,
)


def _prices(index: pd.DatetimeIndex, drift: float, seed: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    steps = 1.0 + drift + rng.normal(0.0, 0.004, len(index))
    return pd.Series(100.0 * np.cumprod(steps), index=index)


# -- the shadow book -------------------------------------------------------


def test_shadow_book_matches_synthesize_nav() -> None:
    """THE anti-drift guarantee (see `shadow_book_nav`): with a single target
    and zero cost, the stepper must reproduce `ratios.synthesize_nav` — the
    M4-validated NAV engine, whose golden numbers were checked against
    Portfolio Visualizer — to the last decimal. If this drifts, every NAV the
    replay reports is measured on a different engine than the rest of the
    system, and the M6 verdict means nothing."""
    index = pd.bdate_range("1991-10-29", periods=900)
    prices = {"SPY": _prices(index, 0.0004, 1), "TLT": _prices(index, 0.0002, 2)}
    rf = pd.Series(0.0001, index=index)
    allocation = {"SPY": 60.0, "TLT": 30.0, "cash": 10.0}

    expected = ratios.synthesize_nav(ratios._normalize_weights(allocation), prices, rf)
    actual, turnover = shadow_book_nav(
        {index[0]: allocation}, prices, rf, cost_bps=0.0, calendar=index
    )

    assert turnover == 0.0
    pd.testing.assert_series_equal(actual, expected)


def test_a_sleeve_that_does_not_exist_yet_is_refused_not_frozen() -> None:
    """The defect measured on the live database on 2026-08-15: the stepper
    applied a return only `if pd.notna(r)`, so a sleeve whose series had not
    STARTED was carried frozen — no gain, no loss, not even rf — while counting
    in the NAV. 17 of the stack's 418 monthly decisions held
    `credit-spread-wide-yield-curve-flat` (50% IWN) before IWN had a price.

    A refusal, not a repair: there is no honest number to substitute for a
    price that does not exist, and the caller's job is to walk a calendar where
    its book can be valued (`priced_calendar`)."""
    index = pd.bdate_range("1991-10-29", periods=400)
    prices = {"SPY": _prices(index, 0.0004, 1), "IWN": _prices(index[200:], 0.0004, 2)}
    rf = pd.Series(0.0001, index=index)

    with pytest.raises(ValueError, match="IWN"):
        shadow_book_nav(
            {index[0]: {"SPY": 50.0, "IWN": 50.0}}, prices, rf, cost_bps=0.0, calendar=index
        )

    # ...and the same book IS priceable once its sleeve exists.
    nav, _ = shadow_book_nav(
        {index[200]: {"SPY": 50.0, "IWN": 50.0}}, prices, rf, cost_bps=0.0, calendar=index
    )
    assert not nav.empty


def test_a_gap_inside_a_series_still_freezes_and_does_not_raise() -> None:
    """The distinction the refusal above rests on. An instrument that did not
    trade on a day the calendar carries is legitimately a 0% day, and the NaN
    that represents it must keep freezing the sleeve. Only a series that has not
    begun is a sleeve nobody could hold."""
    index = pd.bdate_range("1991-10-29", periods=300)
    spy = _prices(index, 0.0004, 1)
    gapped = spy.drop(spy.index[150:155])  # a five-day hole, mid-series
    rf = pd.Series(0.0001, index=index)

    nav, _ = shadow_book_nav(
        {index[0]: {"SPY": 100.0}}, {"SPY": gapped}, rf, cost_bps=0.0, calendar=index
    )
    assert len(nav) == len(index)
    assert nav.notna().all()


def test_priced_calendar_is_the_dates_every_sleeve_has_a_price() -> None:
    """One rule, three callers (`market_signal.stack_calendar`,
    `compute_context`'s reference books, and `ratios.synthesize_nav` stating it
    in its own words). Cash imposes nothing; a sleeve with no series at all
    makes the basket unpriceable, which is NOT the same answer as an all-cash
    basket's and is why the caller special-cases that one."""
    index = pd.bdate_range("1991-10-29", periods=300)
    prices = {"SPY": _prices(index, 0.0004, 1), "IWN": _prices(index[100:], 0.0004, 2)}

    both = replay.priced_calendar(prices, ["SPY", "IWN", "cash"])
    assert both[0] == index[100]
    assert len(both) == 200

    assert list(replay.priced_calendar(prices, ["SPY", "cash"])) == list(index)
    assert replay.priced_calendar(prices, ["SPY", "GLD"]).empty  # GLD has no series
    assert replay.priced_calendar(prices, ["cash"]).empty  # the caller's to read


def test_shadow_book_charges_20bps_on_a_full_switch() -> None:
    """docs/TASKS.md Task 9.1 step 4: cost = `sum(|delta weight|) x cost_bps`,
    the UN-halved sum ("= 2 x turnover; do NOT also x2"). A full switch has
    sum|delta| = 2.0, so at 10 bps it costs exactly 20 bps."""
    index = pd.bdate_range("2000-01-03", periods=60)
    flat = pd.Series(100.0, index=index)
    prices = {"SPY": flat, "TLT": flat.copy()}
    rf = pd.Series(0.0, index=index)
    switch_date = index[30]

    held, _ = shadow_book_nav({index[0]: {"SPY": 100.0}}, prices, rf, 10.0, index)
    switched, turnover = shadow_book_nav(
        {index[0]: {"SPY": 100.0}, switch_date: {"TLT": 100.0}}, prices, rf, 10.0, index
    )

    # Prices are flat, so the ONLY difference between the two books is the cost.
    assert turnover == pytest.approx(1.0)  # sum|delta|/2 = 1.0 = a full rotation
    assert switched.iloc[-1] == pytest.approx(held.iloc[-1] * (1.0 - 0.0020))


def test_shadow_book_costs_are_measured_against_drifted_weights() -> None:
    """The trade the owner really places is from the book's ACTUAL weights, not
    from its last target: after SPY doubles, a 50/50 book is really 67/33, so
    re-targeting 50/50 still trades (and costs)."""
    index = pd.bdate_range("2000-01-03", periods=40)
    rising = pd.Series(np.linspace(100.0, 200.0, len(index)), index=index)
    flat = pd.Series(100.0, index=index)
    prices = {"SPY": rising, "TLT": flat}
    rf = pd.Series(0.0, index=index)

    _, turnover = shadow_book_nav(
        {index[0]: {"SPY": 50.0, "TLT": 50.0}, index[20]: {"SPY": 50.0, "TLT": 50.0}},
        prices,
        rf,
        10.0,
        index,
    )
    assert turnover > 0.0


# -- metrics ---------------------------------------------------------------


def test_cagr_is_the_pinned_annualization() -> None:
    index = pd.bdate_range("2000-01-03", periods=253)
    nav = pd.Series(np.linspace(100.0, 110.0, len(index)), index=index)
    metrics = replay.nav_metrics(nav, pd.Series(0.0, index=index))
    expected = (110.0 / 100.0) ** (ratios.TRADING_DAYS_PER_YEAR / len(index)) - 1.0
    assert metrics.cagr == pytest.approx(expected)


def test_nav_metrics_of_a_too_short_book_are_none() -> None:
    index = pd.bdate_range("2000-01-03", periods=1)
    metrics = replay.nav_metrics(pd.Series([100.0], index=index), pd.Series(0.0, index=index))
    assert metrics == replay.NavMetrics(None, None, None, None)


# -- the decision clock ----------------------------------------------------


def test_decision_dates_step_weekly_within_the_window() -> None:
    calendar = pd.DatetimeIndex(pd.bdate_range("2020-01-01", periods=60))
    dates = replay.decision_dates(calendar, date(2020, 1, 6), date(2020, 2, 7), "weekly")
    assert dates[0] >= pd.Timestamp("2020-01-06")
    assert dates[-1] <= pd.Timestamp("2020-02-07")
    # One decision per calendar week, and every one is a real trading day.
    assert len(dates) == len({(d.year, d.isocalendar().week) for d in dates})
    assert all(d in calendar for d in dates)


def test_decision_dates_episodes_spends_the_llm_budget_on_the_stress_windows() -> None:
    """M8b's cost-bounded cadence. Unlike the fixed steps, `episodes` is a
    CHOICE of dates — an agentic date costs an LLM call — so what it must
    guarantee is that every date falls inside a named window and that the total
    stays inside the milestone's ~20-run budget."""
    calendar = pd.DatetimeIndex(pd.bdate_range("2007-01-01", periods=5200))  # through 2026
    dates = replay.decision_dates(calendar, date(2007, 1, 1), date(2026, 8, 1), "episodes")

    assert 18 <= len(dates) <= 22  # docs/MILESTONES.md M8b: "≈20 LLM runs"
    assert dates == sorted(dates)
    windows = [(opens, closes) for _name, opens, closes in replay.EPISODES]
    assert all(any(o <= d.date() <= c for o, c in windows) for d in dates)
    # monthly INSIDE each window — one decision per month, no denser
    for opens, closes in windows:
        inside = [d for d in dates if opens <= d.date() <= closes]
        assert inside and len(inside) == len({(d.year, d.month) for d in inside})


def test_decision_dates_episodes_are_bounded_by_the_callers_window() -> None:
    """The caller still owns start/end: an episode outside them contributes
    nothing rather than smuggling its dates back in."""
    calendar = pd.DatetimeIndex(pd.bdate_range("2007-01-01", periods=5200))
    dates = replay.decision_dates(calendar, date(2020, 1, 1), date(2020, 12, 31), "episodes")
    assert dates and all(d.year == 2020 for d in dates)


def test_decision_dates_step_quarterly_and_monthly_are_coarser_than_weekly() -> None:
    """The cadences OPEN #2 compares (docs/IMPROVEMENTS.md I-40) must actually
    step at their stated frequency — a silently-wrong clock would make a
    cadence comparison meaningless rather than fail."""
    calendar = pd.DatetimeIndex(pd.bdate_range("2020-01-01", periods=520))  # ~2 years
    window = (date(2020, 1, 6), date(2021, 12, 31))
    weekly, monthly, quarterly = (
        replay.decision_dates(calendar, *window, cadence)
        for cadence in ("weekly", "monthly", "quarterly")
    )
    # One decision per calendar quarter / month, all real trading days in window.
    assert len(quarterly) == len({(d.year, d.quarter) for d in quarterly}) == 8
    assert len(monthly) == len({(d.year, d.month) for d in monthly}) == 24
    assert len(quarterly) < len(monthly) < len(weekly)
    assert all(d in calendar for d in quarterly)


# -- a synthetic 2-portfolio world -----------------------------------------


def _inputs(*, panel_dates: pd.DatetimeIndex, challenger_sortino: float) -> ReplayInputs:
    """A defender and one challenger. The challenger's indicators are constant,
    so the switch decision is a pure function of the thresholds."""
    prices = {"SPY": _prices(panel_dates, 0.0003, 7), "TLT": _prices(panel_dates, 0.0002, 8)}

    def panel(sortino: float, calmar: float, drawdown: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "sharpe_rolling": sortino,
                "sortino_rolling": sortino,
                "calmar_rolling": calmar,
                "drawdown": drawdown,
            },
            index=panel_dates,
        )

    return ReplayInputs(
        panel={
            "defender": panel(0.5, 2.0, -0.08),
            "challenger": panel(challenger_sortino, 2.0, -0.08),
        },
        portfolios={
            "defender": PortfolioMeta(
                "defender", True, "4seasons", None, "four-seasons-rp", {"SPY": 100.0}
            ),
            "challenger": PortfolioMeta(
                "challenger", False, "4seasons", None, "four-seasons-rp", {"TLT": 100.0}
            ),
        },
        prices=prices,
        rf=pd.Series(0.0, index=panel_dates),
        regimes=[],
        backtests=[],
        scenarios=[],
        prescribed={},
        caps=Caps(max_single_asset_pct=100.0, max_drawdown_pct=-15.0),
        # No portfolio carries its own rule here, so `caps_for` returns the
        # user's for both — the I-47 behaviour a test that wants otherwise
        # overrides explicitly (see `test_a_challengers_own_drawdown_rule_binds`).
        portfolio_caps={},
        allowed_tickers=frozenset({"SPY", "TLT", "cash"}),
        initial_defender_id="defender",
    )


def test_replay_switches_when_the_gates_clear_and_holds_when_they_do_not() -> None:
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))

    winner = replay.run_replay(
        _inputs(panel_dates=dates, challenger_sortino=1.5),
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
    )
    assert winner.n_switches == 1  # switches once, then the challenger IS the defender

    # A sortino gap of 0.01 is below `sortino_gap_min` (0.02) -> never switches,
    # so agent-follow is hold-defender exactly.
    flat = replay.run_replay(
        _inputs(panel_dates=dates, challenger_sortino=0.51),
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
    )
    assert flat.n_switches == 0
    pd.testing.assert_series_equal(flat.nav_agent_follow, flat.nav_hold_defender)


def test_acceptance_policy_needs_n_consecutive_confirmations() -> None:
    """'accept-after-2-weeks-confirmation': the same challenger must clear the
    gates on 2 consecutive decision dates before the book moves."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    inputs = _inputs(panel_dates=dates, challenger_sortino=1.5)

    immediate = replay.run_replay(
        inputs,
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=0.0,
        confirmation_weeks=1.0,
    )
    confirmed = replay.run_replay(
        inputs,
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=0.0,
        confirmation_weeks=3.0,
    )
    switch_dates_immediate = [p.date for p in immediate.proposals if p.kind == "switch"]
    switch_dates_confirmed = [p.date for p in confirmed.proposals if p.kind == "switch"]
    # Same decision, later: the confirmation window delays it by 2 more steps.
    assert switch_dates_confirmed[0] > switch_dates_immediate[0]


# -- point-in-time (docs/TASKS.md Task 9.1 `test_replay_point_in_time`) -----


def test_replay_point_in_time() -> None:
    """ "injecting a future-dated row must not change any decision before its
    date" — the behavioural proof that the harness is PIT (the data-level
    checks live in `pit_assertions`).

    A challenger that becomes spectacular in 2005 must not move a single
    decision made in 2000-2001."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=1600))
    base = _inputs(panel_dates=dates, challenger_sortino=0.51)

    future = pd.Timestamp("2005-01-03")
    leaked_panel = {k: v.copy() for k, v in base.panel.items()}
    leaked_panel["challenger"].loc[
        leaked_panel["challenger"].index >= future, "sortino_rolling"
    ] = 9.0
    leaked = dataclasses.replace(base, panel=leaked_panel)

    window = {"start": date(2000, 1, 3), "end": date(2001, 12, 31)}
    clean_result = replay.run_replay(
        base, THRESHOLDS, cost_bps=10.0, confirmation_weeks=2.0, **window
    )
    leaked_result = replay.run_replay(
        leaked, THRESHOLDS, cost_bps=10.0, confirmation_weeks=2.0, **window
    )

    assert clean_result.n_switches == leaked_result.n_switches == 0
    pd.testing.assert_series_equal(clean_result.nav_agent_follow, leaked_result.nav_agent_follow)


def test_the_regime_asof_sees_the_one_the_system_is_actually_in() -> None:
    """I-49, the other half. `_load_regimes` filtered to CLOSED instances —
    right for `_favors_asof`, which aggregates completed periods, and wrong for
    this one, which answers "what would the live detector have shown on that
    Monday". The regime the system is IN is open by definition, so every date
    after the last closure ran under the previous label."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    closed = RegimeInstance(
        "r1",
        "stagflation",
        pd.Timestamp("2000-01-10"),
        pd.Timestamp("2000-06-01"),
        pd.Timestamp("2000-02-10"),
        pd.Timestamp("2000-08-01"),
    )
    open_now = RegimeInstance(
        "r2",
        "rising-growth-falling-inflation",
        pd.Timestamp("2000-06-01"),
        None,  # still running
        pd.Timestamp("2000-08-01"),
        None,  # and therefore not closed
    )
    inputs = dataclasses.replace(
        _inputs(panel_dates=dates, challenger_sortino=0.5), regimes=[closed, open_now]
    )

    # Before the open regime was confirmed, the closed one is the latest known.
    assert replay._regime_asof(inputs, pd.Timestamp("2000-07-01")) == "stagflation"
    # After it, the OPEN one is what the detector shows — the label that used to
    # be invisible here.
    assert (
        replay._regime_asof(inputs, pd.Timestamp("2000-09-01")) == "rising-growth-falling-inflation"
    )
    # ...and it never leaks into FAVORS, which still needs a knowable closure.
    assert replay._favors_asof(inputs, "rising-growth-falling-inflation", dates[-1]) is None


def test_a_benchmark_is_ranked_but_can_never_be_proposed() -> None:
    """The yardstick contract, behavioural half (`BENCHMARK_PORTFOLIOS`, owner
    2026-08-15). Benchmarks are seeded as ordinary enabled portfolios so the
    ranking shows what the apparatus is worth against them — which means they
    reach the challenger search like any other row and have to be refused by
    KIND.

    Not by the caps, and that distinction is the whole reason the kind exists:
    a 100% `spy-USD` fails the concentration gate anyway, but
    `all-weather-USD`'s largest sleeve is 40% and clears it, so the first
    version of this design would have proposed the benchmark as a switch. The
    challenger here is deliberately EXCELLENT (Sortino 2.0 against the
    defender's 0.5) so nothing but the kind can be refusing it."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    base = _inputs(panel_dates=dates, challenger_sortino=2.0)
    ranked = replay.rank_portfolios(replay._valuation_rows_asof(base, "defender", dates[-1]), 0.02)
    defender = next(rr for rr in ranked if rr.row.defender)
    mature = {"challenger", "defender"}

    # It outranks the defender and clears every gate...
    assert ranked[0].row.portfolio_id == "challenger"
    assert (
        replay._best_challenger(ranked, defender, base, THRESHOLDS.proposal, mature) == "challenger"
    )

    # ...and the same row, named a benchmark, is not proposed at all.
    with mock.patch.object(replay, "BENCHMARK_PORTFOLIOS", frozenset({"challenger"})):
        assert replay._best_challenger(ranked, defender, base, THRESHOLDS.proposal, mature) is None
        # Still RANKED, though — visibility is the other half of the contract.
        assert ranked[0].row.portfolio_id == "challenger"


def test_a_challengers_own_drawdown_rule_binds_where_the_user_cap_would_pass() -> None:
    """I-47: gate 4 binds the CHALLENGER, so the caps that apply are the
    stricter of the user's and that portfolio's own. The replay judged every
    candidate against `user_profile` alone, which made it LOOSER than the rule
    on exactly the books the bridge switches between (-15 on the 4s books, -10
    on barbell)."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    base = _inputs(panel_dates=dates, challenger_sortino=2.0)
    ranked = replay.rank_portfolios(replay._valuation_rows_asof(base, "defender", dates[-1]), 0.02)
    challenger = next(rr for rr in ranked if rr.row.portfolio_id == "challenger")
    defender = next(rr for rr in ranked if rr.row.defender)
    mature = {"challenger", "defender"}

    # The user's -15% passes the challenger's -8% drawdown...
    assert (
        replay._best_challenger(ranked, defender, base, THRESHOLDS.proposal, mature) == "challenger"
    )
    # ...and its OWN -5% rule does not, though nothing about the row changed.
    stricter = dataclasses.replace(
        base,
        portfolio_caps={
            "challenger": Caps(max_single_asset_pct=100.0, max_drawdown_pct=-5.0),
        },
    )
    assert replay._best_challenger(ranked, defender, stricter, THRESHOLDS.proposal, mature) is None
    assert challenger.row.max_drawdown == pytest.approx(-0.08)  # the row itself is unchanged


def test_favors_asof_ignores_regimes_not_yet_confirmed() -> None:
    """FAVORS as-of t aggregates ONLY over instances `created_at <= t AND
    closed_at <= t`. Both are CONFIRMING PRINTS: a regime that began
    (start_date) before t but was confirmed after it must stay invisible, else
    the `regime_confirm_prints` hysteresis window leaks — and a regime whose
    retroactive `end_date` fell before t stays invisible too until the prints
    that made its CLOSURE knowable have landed (I-49)."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    inputs = dataclasses.replace(
        _inputs(panel_dates=dates, challenger_sortino=0.51),
        regimes=[
            RegimeInstance(
                regime_id="r1",
                regime_type_id="stagflation",
                start_date=pd.Timestamp("2000-01-10"),
                end_date=pd.Timestamp("2000-06-01"),
                created_at=pd.Timestamp("2000-09-01"),  # confirmed 3 months later
                closed_at=pd.Timestamp("2000-09-15"),  # its successor confirmed later still
            )
        ],
        backtests=[replay.BacktestRow("four-seasons-rp", "r1", 1.2)],
    )
    # Closed (end_date) but NOT yet confirmed -> invisible.
    assert replay._favors_asof(inputs, "stagflation", pd.Timestamp("2000-07-01")) is None
    # Confirmed, and its retroactive end is long past — but the closure itself
    # was not knowable until 09-15, so it is STILL invisible (I-49).
    assert replay._favors_asof(inputs, "stagflation", pd.Timestamp("2000-09-05")) is None
    # Closure knowable -> visible.
    assert (
        replay._favors_asof(inputs, "stagflation", pd.Timestamp("2000-10-01")) == "four-seasons-rp"
    )


def test_pit_assertions_catch_a_backdated_confirmation() -> None:
    """A `created_at` back-dated to `start_date` is exactly the hysteresis leak
    Task 9.1 names — `pit_assertions` must refuse it rather than certify a run
    that silently saw the future."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=100))
    good = RegimeInstance(
        "r1",
        "stagflation",
        pd.Timestamp("2000-01-10"),
        pd.Timestamp("2000-06-01"),
        pd.Timestamp("2000-02-10"),
        pd.Timestamp("2000-08-01"),
    )
    inputs = dataclasses.replace(_inputs(panel_dates=dates, challenger_sortino=0.5), regimes=[good])
    assert replay.pit_assertions(inputs, [pd.Timestamp("2000-05-01")])

    leaky = dataclasses.replace(good, created_at=pd.Timestamp("2000-01-05"))  # before start_date
    assert not replay.pit_assertions(
        dataclasses.replace(inputs, regimes=[leaky]), [pd.Timestamp("2000-05-01")]
    )


def test_active_scenario_prefers_bear_and_falls_back_to_base() -> None:
    """The mechanical active-scenario rule (`_active_scenario`): none fires ->
    'base' (the residual); several fire -> bear > bull."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=100))
    calendar = pd.date_range("2000-01-03", periods=200, freq="D")
    inputs = dataclasses.replace(
        _inputs(panel_dates=dates, challenger_sortino=0.5),
        scenarios=[
            ScenarioMeta(
                "sc-base", "four-seasons-rp", "base", {"SPY": 100.0}, pd.Series(dtype=bool)
            ),
            ScenarioMeta(
                "sc-bull",
                "four-seasons-rp",
                "bull",
                {"SPY": 80.0, "TLT": 20.0},
                pd.Series(True, index=calendar),
            ),
            ScenarioMeta(
                "sc-bear",
                "four-seasons-rp",
                "bear",
                {"TLT": 100.0},
                pd.Series(calendar >= pd.Timestamp("2000-03-01"), index=calendar),
            ),
        ],
    )
    # Only bull fires -> bull.
    active = replay._active_scenario(inputs, "four-seasons-rp", pd.Timestamp("2000-02-01"))
    assert active is not None and active.name == "bull"
    # Both fire -> bear wins (risk-first).
    active = replay._active_scenario(inputs, "four-seasons-rp", pd.Timestamp("2000-04-01"))
    assert active is not None and active.name == "bear"


def test_favors_leg_never_pulls_toward_another_strategy() -> None:
    """The own-strategy guard (M6 finding, `_reallocation_target`): blending
    toward a DIFFERENT strategy's prescribed allocation is a half-switch by the
    back door — it changes strategy exposure while bypassing all 5 switch gates.
    Here the top-FAVORS strategy is 'other-strategy', so the FAVORS leg must
    contribute nothing and the blend must stay put."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    inputs = dataclasses.replace(
        _inputs(panel_dates=dates, challenger_sortino=0.51),
        regimes=[
            RegimeInstance(
                "r1",
                "stagflation",
                pd.Timestamp("2000-01-10"),
                pd.Timestamp("2000-06-01"),
                pd.Timestamp("2000-06-15"),
                pd.Timestamp("2000-06-15"),
            )
        ],
        # 'other-strategy' dominates FAVORS and prescribes a wildly different book.
        backtests=[replay.BacktestRow("other-strategy", "r1", 9.9)],
        prescribed={"other-strategy": {"TLT": 100.0}, "four-seasons-rp": {"SPY": 100.0}},
        scenarios=[
            ScenarioMeta(
                "sc-base", "four-seasons-rp", "base", {"SPY": 100.0}, pd.Series(dtype=bool)
            )
        ],
    )
    target = replay._reallocation_target(
        inputs,
        "defender",
        {"SPY": 100.0},
        pd.Timestamp("2000-09-01"),
        THRESHOLDS.proposal,
        "base",
    )
    assert target is None


def test_scenario_hysteresis_ignores_a_one_week_trigger_flicker() -> None:
    """A scenario that fires for a single week must not move the book: it takes
    `confirmation_weeks` consecutive dates to become the confirmed scenario
    (the M3 detector's remedy, applied to the same disease)."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    calendar = pd.date_range("2000-01-03", periods=700, freq="D")
    # 'bear' fires for exactly 3 calendar days, then never again.
    flicker = pd.Series(
        (calendar >= pd.Timestamp("2000-03-01")) & (calendar <= pd.Timestamp("2000-03-03")),
        index=calendar,
    )
    inputs = dataclasses.replace(
        _inputs(panel_dates=dates, challenger_sortino=0.51),
        scenarios=[
            ScenarioMeta(
                "sc-base", "four-seasons-rp", "base", {"SPY": 100.0}, pd.Series(dtype=bool)
            ),
            ScenarioMeta("sc-bear", "four-seasons-rp", "bear", {"TLT": 100.0}, flicker),
        ],
        prescribed={"four-seasons-rp": {"SPY": 100.0}},
    )
    result = replay.run_replay(
        inputs,
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
    )
    assert [p for p in result.proposals if p.kind == "reallocation"] == []


def test_an_immature_portfolio_stays_ranked_but_cannot_challenge() -> None:
    """`MIN_CANDIDACY_OBS` (M6 finding): the 1991-92 warm-up switched on a
    Sortino of 7.5 computed over 10 observations. A book with too little history
    is unmeasured, not good — it may not challenge until it has a year."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    inputs = _inputs(panel_dates=dates, challenger_sortino=1.5)
    early = replay.run_replay(
        inputs,
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2000, 6, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
    )
    # The whole window sits inside the challenger's first 252 observations.
    assert early.n_switches == 0

    late = replay.run_replay(
        inputs,
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
    )
    first_switch = min(p.date for p in late.proposals if p.kind == "switch")
    assert first_switch >= dates[replay.MIN_CANDIDACY_OBS - 1]


def test_regime_signal_switches_to_the_designed_book_on_a_confirmed_flip() -> None:
    """`switch_signal='regime'` (M6 A/B): a confirmed regime flip nominates the
    DESIGNED_FOR book; the veto gates accept it; the switch happens on the
    first decision date the regime is VISIBLE (created_at), not its back-dated
    start_date."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=700))
    base = _inputs(panel_dates=dates, challenger_sortino=0.5)
    portfolios = dict(base.portfolios)
    portfolios["challenger"] = dataclasses.replace(
        portfolios["challenger"], designed_regime_type_id="stagflation"
    )
    inputs = dataclasses.replace(
        base,
        portfolios=portfolios,
        regimes=[
            RegimeInstance(
                "r1",
                "stagflation",
                pd.Timestamp("2001-02-05"),
                pd.Timestamp("2001-12-01"),
                pd.Timestamp("2001-05-07"),
                pd.Timestamp("2001-12-01"),
            )
        ],
    )
    result = replay.run_replay(
        inputs,
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
        switch_signal="regime",
    )
    assert result.n_switches == 1
    switch = next(p for p in result.proposals if p.kind == "switch")
    assert switch.portfolio_id == "challenger"
    # Visible at created_at (2001-05-07), NEVER at start_date (2001-02-05).
    assert switch.date >= pd.Timestamp("2001-05-07")


def test_regime_signal_holds_when_no_book_is_designed_for_the_regime() -> None:
    """'uncertain' (and any unmapped type) nominates nobody — the variant must
    HOLD rather than fall back to the stale ranking discoverer, else the A/B
    would not isolate the regime signal."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=700))
    inputs = dataclasses.replace(
        _inputs(panel_dates=dates, challenger_sortino=1.5),  # ranking would switch
        regimes=[
            RegimeInstance(
                "r1",
                "uncertain",
                pd.Timestamp("2000-02-07"),
                pd.Timestamp("2001-12-01"),
                pd.Timestamp("2000-05-01"),
                pd.Timestamp("2001-12-01"),
            )
        ],
    )
    result = replay.run_replay(
        inputs,
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
        switch_signal="regime",
    )
    assert result.n_switches == 0


def test_regime_signal_veto_gates_still_block_a_designed_book() -> None:
    """The gates drop to VETO duty, they do not disappear: a designed book
    breaching the user drawdown rule stays unswitchable."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=700))
    base = _inputs(panel_dates=dates, challenger_sortino=0.5)
    panel = {k: v.copy() for k, v in base.panel.items()}
    panel["challenger"]["drawdown"] = -0.30  # breaches the -15% rule
    portfolios = dict(base.portfolios)
    portfolios["challenger"] = dataclasses.replace(
        portfolios["challenger"], designed_regime_type_id="stagflation"
    )
    inputs = dataclasses.replace(
        base,
        panel=panel,
        portfolios=portfolios,
        regimes=[
            RegimeInstance(
                "r1",
                "stagflation",
                pd.Timestamp("2000-02-07"),
                pd.Timestamp("2001-12-01"),
                pd.Timestamp("2000-05-01"),
                pd.Timestamp("2001-12-01"),
            )
        ],
    )
    result = replay.run_replay(
        inputs,
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
        switch_signal="regime",
    )
    assert result.n_switches == 0


def test_compute_context_finds_the_defensive_pole_and_matches_risk() -> None:
    """`ReplayContext` (M6 verification finding): the pole is the least-negative
    static-mdd book, and the matched-risk blend's drawdown lands close to A's."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    inputs = _inputs(panel_dates=dates, challenger_sortino=1.5)
    result = replay.run_replay(
        inputs,
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
    )
    context = replay.compute_context(inputs, result)
    assert context is not None
    assert context.defensive_pole_id in inputs.portfolios
    assert context.static_matched_risk.max_drawdown is not None
    mdd_a = result.metrics_agent_follow.max_drawdown
    assert mdd_a is not None
    # The 0.05-step blend grid should land within a couple of points of A.
    assert abs(context.static_matched_risk.max_drawdown - mdd_a) < 0.05


def test_both_arms_start_from_the_same_seeded_defender() -> None:
    """ "A and B START FROM THE SAME seeded defender at t=start — they diverge
    ONLY because A applies the mechanical proposals" (docs/TASKS.md Task 9.1).
    The gate isolates the marginal value of adaptation, nothing else."""
    dates = pd.DatetimeIndex(pd.bdate_range("2000-01-03", periods=500))
    result = replay.run_replay(
        _inputs(panel_dates=dates, challenger_sortino=1.5),
        THRESHOLDS,
        start=date(2000, 1, 3),
        end=date(2001, 11, 30),
        cost_bps=10.0,
        confirmation_weeks=2.0,
    )
    first_switch = min(p.date for p in result.proposals if p.kind == "switch")
    before = result.nav_agent_follow.index < first_switch
    pd.testing.assert_series_equal(
        result.nav_agent_follow[before], result.nav_hold_defender[before]
    )
