"""M6 unit tests for the shadow replay (docs/TASKS.md Task 9.1; docs/MILESTONES.md
M6 Definition of Verified) — the pure core of `mechanical/replay.py`, plus the
two tests Task 9.1 names explicitly.
"""

import dataclasses
from datetime import date

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


def test_favors_asof_ignores_regimes_not_yet_confirmed() -> None:
    """FAVORS as-of t aggregates ONLY over instances `created_at <= t AND
    end_date < t`. `created_at` is the CONFIRMING PRINT — a regime that began
    (start_date) before t but was confirmed after it must stay invisible, else
    the `regime_confirm_prints` hysteresis window leaks."""
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
            )
        ],
        backtests=[replay.BacktestRow("four-seasons-rp", "r1", 1.2)],
    )
    # Closed (end_date) but NOT yet confirmed -> invisible.
    assert replay._favors_asof(inputs, "stagflation", pd.Timestamp("2000-07-01")) is None
    # Confirmed and closed -> visible.
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
