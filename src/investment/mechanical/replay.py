"""Shadow replay — the meta-backtest of the agent (docs/TASKS.md Phase 9 Task
9.1; docs/MILESTONES.md M6, a STOP POINT and the mechanical PREMISE GATE).

The mechanical core produces COMPLETE decisions on its own (switches are the 5
gates, reallocation is the scenario/FAVORS blend), so it can be replayed
standalone week by week over the 35y backfill to answer the founding mantra's
question: does MECHANICAL REGIME-ROTATION OVER A FIXED PORTFOLIO MENU, net of
costs, beat holding a static All Weather? Two NAVs, same seeded defender at
t=start, diverging ONLY because A applies the gated proposals:

  A. agent-follow          (accept every gated proposal)
  B. hold-initial-defender (never switch)

B is a FAIR baseline because four-seasons-rp is risk parity — regime-AGNOSTIC
by design, so holding it across 35y is "do nothing", not "hold a bet on the
2000 regime".

POINT-IN-TIME DISCIPLINE (non-negotiable — a leak invalidates everything):
  - MarketData/derivatives: rows are PIT by construction (as-known-at-ts,
    ADR-003 first-release vintages) -> `ts <= t` is the whole rule.
  - Portfolio indicators: `portfolio_nav`'s persisted rolling_* columns are
    TRAILING windows, so the row AS-OF t is knowable at t. `rolling_window_days`
    is not in the Task 9.2 grid, so these need no recomputation per combo.
  - Regimes: visibility keys on `created_at` — the CONFIRMING PRINT's date
    (`market/regime.py` `_commit_regime` dates it "PIT-correct ... at the
    historical confirming print"), NOT `start_date`, which is back-dated to the
    data. A regime begun before t but not yet confirmed at t stays invisible,
    else a few weeks of look-ahead leak.
  - FAVORS as-of t: re-aggregated from `backtest` rows over regime instances
    with `created_at <= t AND end_date < t` — never read from the live seeded
    `favors` edges, which aggregate the WHOLE 35y.

The M6 replay is deliberately BLIND to invariant weights (no Worker, nothing
cited — docs/ARCHITECTURE.md), which is why N_min/theta/confidence are not in
the calibration grid. The Worker's marginal contribution is measured
separately by the AGENTIC replay (Task 9.4 / M8b): the SAME `replay()` gains
an `include_worker=True` flag there — a second decision loop must never be
written (docs/TASKS.md Task 9.4: "so replay logic cannot DRIFT from live
logic — the classic replay bug"). The gates it drives already live in
`mechanical/gates.py` for exactly that reason.

Split like every mechanical module: a PURE core (`run_replay` and the shadow
book, directly unit-testable) and a thin async DB layer (`load_inputs`,
`replay`).
"""

import argparse
import asyncio
import dataclasses
import json
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd
from ulid import ULID

from investment.config import Settings
from investment.db.sqlite import InvestmentDB
from investment.mechanical import backtests, gates, ratios
from investment.mechanical.gates import Caps, ProposalThresholds
from investment.mechanical.scenarios import (
    Conjunction,
    evaluate_trigger_series,
    parse_trigger_conjunction,
)
from investment.mechanical.snapshots import RankedRow, ValuationRow, rank_portfolios

# ADR-003: the whole point of the ALFRED first-release backfill. A go-live
# verdict obtained on REVISED data is not valid evidence, so the report records
# which vintage regime produced it.
VINTAGE_FIRST_RELEASE = "first_release"
ACCEPTANCE_POLICY = "accept-after-{weeks:g}-weeks-confirmation"
KIND_MECHANICAL = "mechanical"

# The scenario names seeded per Strategy (db/seed_data.py SCENARIOS).
SCENARIO_BASE = "base"
# Mechanical active-scenario priority — see `_active_scenario`.
SCENARIO_PRIORITY = ("bear", "bull", SCENARIO_BASE)

# Candidacy maturity floor (M6 finding): a portfolio needs at least this many
# NAV observations as-of t before it may CHALLENGE. Without it, the 1991-92
# warm-up ranked books on a Sortino of 7.5 computed over 10 observations and
# switched on it — numbers that pass `min_periods=2` arithmetic but carry no
# evidential weight. One trading year is the smallest window in the pinned
# indicator family (RETURN_WINDOWS_DAYS' return_1y), reused rather than
# invented. A CANDIDACY rule, not a ranking rule: every enabled portfolio
# stays ranked together (CLAUDE.md "Ranking rule"), exactly like the user
# drawdown rule keeps a breaching row ranked but not proposable. NOT in the
# Task 9.2 grid: a maturity guard, not a performance dial (same reasoning as
# `proposal_min_allocation_change_pts`).
MIN_CANDIDACY_OBS = 252


# -- inputs (loaded once, PIT-filtered in memory) ---------------------------


@dataclasses.dataclass(frozen=True)
class ReplayThresholds:
    """The Task 9.2 calibration grid's knobs, in ONE record so a grid search
    cannot vary half a set.

    `tiebreak_window` rides here rather than inside `ProposalThresholds`
    because it is a RANKING knob, not a proposal gate — `gates.py` has no
    business knowing about it — but Task 9.2 calibrates the two together."""

    proposal: ProposalThresholds
    tiebreak_window: float


@dataclasses.dataclass(frozen=True)
class PortfolioMeta:
    portfolio_id: str
    defender: bool
    framework_id: str
    designed_regime_type_id: str | None
    primary_strategy_id: str | None
    allocation: dict[str, float]


@dataclasses.dataclass(frozen=True)
class RegimeInstance:
    regime_id: str
    regime_type_id: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    created_at: pd.Timestamp
    """`created_at` = the confirming print's date = when this regime became
    KNOWN. THE PIT visibility key (see module docstring). `start_date` is
    back-dated to the DATA and must never be used for visibility — it is
    carried here only so `pit_assertions` can check the two against each
    other."""


@dataclasses.dataclass(frozen=True)
class BacktestRow:
    strategy_id: str
    regime_id: str
    sortino_rolling: float | None


@dataclasses.dataclass(frozen=True)
class ScenarioMeta:
    scenario_id: str
    strategy_id: str
    name: str
    target_allocation: dict[str, float]
    active: pd.Series
    """Daily boolean series: does this scenario's numeric trigger DISJUNCTION
    hold? (`mechanical/scenarios.py` owns the grammar and the disjunction
    semantics.) Levels are forward-filled from `market_data`, so reading it at
    t is a `ts <= t` read — PIT."""


@dataclasses.dataclass(frozen=True)
class ReplayInputs:
    """Everything `run_replay` reads, loaded ONCE so the Task 9.2 grid search
    can run hundreds of threshold combos without re-querying SQLite."""

    panel: dict[str, pd.DataFrame]
    portfolios: dict[str, PortfolioMeta]
    prices: dict[str, pd.Series]
    rf: pd.Series
    regimes: list[RegimeInstance]
    backtests: list[BacktestRow]
    scenarios: list[ScenarioMeta]
    prescribed: dict[str, dict[str, float]]
    caps: Caps
    allowed_tickers: frozenset[str]
    initial_defender_id: str


# -- pure core: the shadow book --------------------------------------------


def shadow_book_nav(
    targets: Mapping[pd.Timestamp, Mapping[str, float]],
    prices: Mapping[str, pd.Series],
    rf: pd.Series,
    cost_bps: float,
    calendar: pd.DatetimeIndex,
) -> tuple[pd.Series, float]:
    """NAV of a book whose TARGET allocation changes over time, net of trade
    costs — `ratios.synthesize_nav`'s pinned conventions (constant target
    weights, monthly rebalance on the first trading day, cash accrues at
    `rf_daily`, NAV(t0)=100) generalized to a time-varying target.

    `targets` maps an effective date -> percent weights summing to 100; the
    earliest one seeds the book. `ratios.synthesize_nav` is the special case of
    a single target with `cost_bps=0`, which `test_shadow_book_matches_synthesize_nav`
    pins exactly — that equality is what keeps this stepper from drifting away
    from the M4-validated NAV engine rather than a shared code path (the engine
    is validated against Portfolio Visualizer's golden numbers; re-cutting it
    to be generic would put that validation at risk for no gain).

    COST MODEL (docs/TASKS.md Task 9.1 step 4): `cost = sum(|delta weight|) x
    replay_cost_bps` — the UN-halved sum, "= 2 x turnover; do NOT also x2",
    because the bps are charged per SIDE. A full switch (sum|delta| = 2.0)
    costs 20 bps. Deltas are measured against the book's ACTUAL drifted
    weights, not its previous target: that is the trade the owner would really
    place. TRADE FEES ONLY — taxes are deliberately out of scope (assume a
    tax-advantaged wrapper).

    Returns (nav, total_turnover) where turnover is the summed `sum|delta|/2`
    over every rebalance to a NEW target (monthly drift-rebalances are not
    counted: they are the baseline both arms pay)."""
    change_dates = sorted(targets)
    if not change_dates or len(calendar) < 2:
        return pd.Series(dtype=float), 0.0
    index = calendar[calendar >= change_dates[0]]
    if len(index) < 2:
        return pd.Series(dtype=float), 0.0

    returns = {t: p.reindex(index).pct_change() for t, p in prices.items()}
    rf_aligned = rf.reindex(index).ffill().fillna(0.0)

    weights = _fractions(targets[change_dates[0]])
    non_cash = [t for t in weights if t != ratios.CASH_TICKER]
    sleeve: dict[str, float] = {t: weights[t] * 100.0 for t in non_cash}
    cash_value = weights.get(ratios.CASH_TICKER, 0.0) * 100.0

    nav = pd.Series(0.0, index=index)
    nav.iloc[0] = 100.0
    prev_period = index[0].to_period("M")
    pending = [d for d in change_dates[1:]]
    total_turnover = 0.0

    for i in range(1, len(index)):
        today = index[i]
        # Rebalancing happens BEFORE the day's own return is applied, on the
        # PREVIOUS day's total — `ratios.synthesize_nav`'s pinned sequencing
        # ("the portfolio enters the month already rebalanced", Portfolio
        # Visualizer's convention). Applying the return first and rebalancing
        # after silently drifts away from the M4-validated engine, which is what
        # `test_shadow_book_matches_synthesize_nav` exists to catch.
        total = sum(sleeve.values()) + cash_value

        # 1. a NEW target effective today (or on a skipped non-trading day)
        #    -> trade into it and pay the cost.
        due = [d for d in pending if d <= today]
        if due:
            for d in due:
                pending.remove(d)
            weights = _fractions(targets[due[-1]])
            actual = _actual_fractions(sleeve, cash_value, total)
            turnover_sum = sum(
                abs(weights.get(t, 0.0) - actual.get(t, 0.0)) for t in set(weights) | set(actual)
            )
            total_turnover += turnover_sum / 2.0
            total *= 1.0 - turnover_sum * cost_bps / 10_000.0
            non_cash = [t for t in weights if t != ratios.CASH_TICKER]
            sleeve = {t: weights[t] * total for t in non_cash}
            cash_value = weights.get(ratios.CASH_TICKER, 0.0) * total
            prev_period = today.to_period("M")

        # 2. monthly drift-rebalance to the STANDING target, at no cost — the
        #    convention `ratios.synthesize_nav` pins (both arms pay it equally,
        #    so charging it would only add noise to A - B).
        elif today.to_period("M") != prev_period:
            sleeve = {t: weights[t] * total for t in non_cash}
            cash_value = weights.get(ratios.CASH_TICKER, 0.0) * total
            prev_period = today.to_period("M")

        # 3. the day's return, on the (possibly just rebalanced) sleeves.
        for t in list(sleeve):
            r = returns[t].iloc[i] if t in returns else np.nan
            if pd.notna(r):
                sleeve[t] *= 1.0 + r
        cash_value *= 1.0 + rf_aligned.iloc[i]

        nav.iloc[i] = sum(sleeve.values()) + cash_value

    return nav, total_turnover


def _fractions(allocation: Mapping[str, float]) -> dict[str, float]:
    total = sum(allocation.values())
    return {t: w / total for t, w in allocation.items()} if total else {}


def _actual_fractions(
    sleeve: Mapping[str, float], cash_value: float, total: float
) -> dict[str, float]:
    if total <= 0:
        return {}
    actual = {t: v / total for t, v in sleeve.items()}
    if cash_value:
        actual[ratios.CASH_TICKER] = cash_value / total
    return actual


# -- pure core: metrics -----------------------------------------------------


@dataclasses.dataclass(frozen=True)
class NavMetrics:
    cagr: float | None
    sortino: float | None
    calmar: float | None
    max_drawdown: float | None

    def as_map(self) -> dict[str, float | None]:
        return dataclasses.asdict(self)


def nav_metrics(nav: pd.Series, rf: pd.Series) -> NavMetrics:
    """Whole-window CAGR/sortino/calmar/max_drawdown as decimal fractions
    (docs/DATA_MODELS.md replay_report: "each: cagr, sortino, calmar,
    max_drawdown — decimal fractions"), reusing `backtests.period_metrics` so
    the replay's headline numbers come out of the same pinned formulas as every
    other measurement in the system (CLAUDE.md: "two implementations must
    produce the same numbers")."""
    if len(nav) < 2:
        return NavMetrics(None, None, None, None)
    period = backtests.period_metrics(nav, rf.reindex(nav.index).ffill())
    return NavMetrics(
        cagr=_cagr(nav),
        sortino=period.sortino_rolling,
        calmar=period.calmar_rolling,
        max_drawdown=period.max_drawdown,
    )


def _cagr(nav: pd.Series) -> float | None:
    """`(NAV_end/NAV_start)^(252/n) - 1` on the observation count — the same
    annualization `ratios.rolling_calmar` uses for its numerator, so CAGR and
    the reported Calmar cannot tell different stories about the same book."""
    n = len(nav)
    if n < 2 or nav.iloc[0] == 0:
        return None
    return ratios.flt((nav.iloc[-1] / nav.iloc[0]) ** (ratios.TRADING_DAYS_PER_YEAR / n) - 1.0)


# -- pure core: the weekly decision loop ------------------------------------


@dataclasses.dataclass(frozen=True)
class ShadowProposal:
    """One gated proposal the agent-follow arm acted on."""

    date: pd.Timestamp
    kind: str  # 'switch' | 'reallocation'
    from_allocation: dict[str, float]
    to_allocation: dict[str, float]
    portfolio_id: str
    verdict: str | None = None  # 'won' | 'lost' — resolved at +12w


@dataclasses.dataclass(frozen=True)
class ReplayContext:
    """The static-frontier context arms (M6 verification finding): A spends
    ~60% of its time in the menu's two most defensive books, so 'A improves
    risk' is only evidence of ADAPTATION if A beats a naive STATIC de-risking
    with the same drawdown. These two arms make every future report answer
    that automatically instead of trusting the analyst:
      - `static_matched_risk` — the initial-defender/defensive-pole blend whose
        whole-window max_drawdown is closest to A's. If A does not beat it,
        the rotation added nothing a fixed allocation could not.
      - `static_best` — the best in-menu static book (highest CAGR among books
        whose drawdown is no worse than A's; None if no book qualifies). The
        sharpest in-menu domination check, with the honest caveat that picking
        it is in-sample."""

    defensive_pole_id: str
    matched_risk_weight: float
    static_matched_risk: NavMetrics
    static_best_id: str | None
    static_best: NavMetrics | None


@dataclasses.dataclass(frozen=True)
class ReplayResult:
    nav_agent_follow: pd.Series
    nav_hold_defender: pd.Series
    metrics_agent_follow: NavMetrics
    metrics_hold_defender: NavMetrics
    proposals: list[ShadowProposal]
    n_switches: int
    avg_turnover: float
    hit_rate_12w: float | None
    false_signal_rate: float | None
    pit_assertions_passed: bool
    # Attached by `replay()` (the async entry) only — never by `run_replay`,
    # so the Task 9.2 grid search does not pay ~30 extra book NAVs per combo.
    context: ReplayContext | None = None


def decision_dates(
    calendar: pd.DatetimeIndex, start: date, end: date, cadence: str
) -> list[pd.Timestamp]:
    """The simulated clock (docs/TASKS.md Task 9.4 "(a) a SIMULATED clock
    stepping `cadence` over [start,end]"). Anchored on the trading calendar:
    every Monday that is a trading day, or the first trading day of the week
    when Monday is a holiday — the live chain's Monday cadence, which is what
    the replay accelerates.

    `quarterly` exists to measure the cadence OPEN #2 needs (docs/V1_STRATEGY.md:
    the Swiss Circular-36 6-month safe harbour wants longer holdings). Faber's
    rebalancing evidence — monthly vs never differs by <0.50%/yr — says slowing
    a STATIC rebalance is nearly free; it says nothing about slowing a SIGNAL,
    which is why this is measured rather than assumed."""
    window = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    if window.empty:
        return []
    frame = pd.Series(window, index=window)
    freq = {"weekly": "W", "monthly": "ME", "quarterly": "QE"}[cadence]
    return [pd.Timestamp(d) for d in frame.resample(freq).first().dropna()]


def _favors_asof(inputs: ReplayInputs, regime_type_id: str, t: pd.Timestamp) -> str | None:
    """The top-FAVORS strategy for `regime_type_id` KNOWABLE at t: re-aggregate
    the `backtest` rows over regime instances confirmed AND closed before t
    (`created_at <= t AND end_date < t`), never the live `favors` edges (which
    aggregate the whole 35y — reading them would leak the future into every
    decision).

    Ranked on mean `sortino_rolling`, matching `backtests.aggregate_metrics`'
    equal-weight-per-instance aggregation and the ranking rule's primary key.

    M6's DoV reads the CALIBRATED weight on this leg against I-35: the
    per-regime ranking it produces is indistinguishable from random regime
    labels in 4 of 5 regimes, so a high stable `blend_favors_weight` on the
    holdout is SUSPICIOUS, not confirmation."""
    visible = {
        r.regime_id
        for r in inputs.regimes
        if r.regime_type_id == regime_type_id and r.created_at <= t and r.end_date < t
    }
    if not visible:
        return None
    by_strategy: dict[str, list[float]] = {}
    for row in inputs.backtests:
        if row.regime_id in visible and row.sortino_rolling is not None:
            by_strategy.setdefault(row.strategy_id, []).append(row.sortino_rolling)
    if not by_strategy:
        return None
    # Ties broken by strategy id — a content-independent, reproducible order
    # (the same reason `snapshots._valuation_rows` pins ORDER BY portfolio.id).
    return max(sorted(by_strategy), key=lambda s: float(np.mean(by_strategy[s])))


def _regime_asof(inputs: ReplayInputs, t: pd.Timestamp) -> str | None:
    """The regime type KNOWN at t = the most recently CONFIRMED instance
    (`created_at <= t`), which is what the live detector would have been
    showing on that Monday."""
    visible = [r for r in inputs.regimes if r.created_at <= t]
    if not visible:
        return None
    return max(visible, key=lambda r: (r.created_at, r.regime_id)).regime_type_id


def _active_scenario(
    inputs: ReplayInputs, strategy_id: str, t: pd.Timestamp
) -> ScenarioMeta | None:
    """The active scenario at t from NUMERIC triggers only — "the Worker's
    qualitative judgment is NOT simulated" (docs/TASKS.md Task 9.1 step 3).

    Judgment call, spec silent (CLAUDE.md "state assumptions explicitly") on
    what to do when several fire or none do:
      - NONE fires -> 'base'. This is exactly how `scenarios.py`'s warm-start
        already reads a base case: the RESIDUAL, "neither of the OTHER
        scenarios' conditions held". It also makes the common week a no-op:
        base's target IS the strategy's prescribed allocation, so
        scenario_delta = 0 and the blend reduces to its FAVORS leg.
      - SEVERAL fire -> bear > bull > base. The seeded triggers genuinely
        overlap (bull is `CPI_YOY < 2.5 AND GROWTH_COMPOSITE > 102`, bear is
        `^VIX > 25 OR ...` — a VIX spike inside a benign print fires both), and
        reading the bear is the risk-first reading: a bear case is
        "alternative routes to the same damage" (scenarios.py)."""
    fired = [
        s
        for s in inputs.scenarios
        if s.strategy_id == strategy_id and _is_active(s.active, t) and s.name != SCENARIO_BASE
    ]
    by_name = {s.name: s for s in inputs.scenarios if s.strategy_id == strategy_id}
    for name in SCENARIO_PRIORITY:
        if name == SCENARIO_BASE:
            return by_name.get(SCENARIO_BASE)
        match = next((s for s in fired if s.name == name), None)
        if match is not None:
            return match
    return None


def _is_active(active: pd.Series, t: pd.Timestamp) -> bool:
    """As-of read: the latest trigger evaluation at or before t (`ts <= t`)."""
    if active.empty:
        return False
    eligible = active.index[active.index <= t]
    return bool(active.loc[eligible[-1]]) if len(eligible) else False


def _valuation_rows_asof(
    inputs: ReplayInputs, defender_id: str, t: pd.Timestamp
) -> list[ValuationRow]:
    """Task 9.1 step 1: "rank enabled portfolios (same snapshots.py code path)".
    Indicators come from `portfolio_nav` AS-OF t — trailing rolling windows, so
    knowable at t. A portfolio with no row yet (its constituents' prices start
    later — e.g. EEM joins in 2003) simply has no indicators and ranks last by
    `snapshots._indicator`, which is the honest treatment: it was not
    investable then.

    `return_*`/`volatility` are left None: `rank_portfolios` never reads them,
    and the replay writes no snapshot rows (docs/TASKS.md Task 9.4: the replay
    "never commits fake events/vertices to the live graph")."""
    rows: list[ValuationRow] = []
    for portfolio_id in sorted(inputs.portfolios):
        meta = inputs.portfolios[portfolio_id]
        frame = inputs.panel.get(portfolio_id)
        asof = _row_asof(frame, t) if frame is not None else None
        rows.append(
            ValuationRow(
                portfolio_id=portfolio_id,
                defender=portfolio_id == defender_id,
                framework_id=meta.framework_id,
                designed_regime_type_id=meta.designed_regime_type_id,
                primary_strategy_id=meta.primary_strategy_id,
                allocation=meta.allocation,
                sharpe_rolling=_get(asof, "sharpe_rolling"),
                sortino_rolling=_get(asof, "sortino_rolling"),
                calmar_rolling=_get(asof, "calmar_rolling"),
                max_drawdown=_get(asof, "drawdown"),
                volatility=None,
                return_3m=None,
                return_6m=None,
                return_1y=None,
                return_3y=None,
                return_5y=None,
            )
        )
    return rows


def _row_asof(frame: pd.DataFrame, t: pd.Timestamp) -> "pd.Series[Any] | None":
    eligible = frame.index[frame.index <= t]
    return frame.loc[eligible[-1]] if len(eligible) else None


def _get(row: "pd.Series[Any] | None", column: str) -> float | None:
    return None if row is None else ratios.flt(row[column])


def run_replay(
    inputs: ReplayInputs,
    thresholds: ReplayThresholds,
    *,
    start: date,
    end: date,
    cost_bps: float,
    confirmation_weeks: float,
    cadence: str = "weekly",
    switch_signal: str = "ranking",
) -> ReplayResult:
    """The PURE mechanical decision loop over [start, end] — the live weekly
    chain accelerated. Per simulated Monday t:
      1. rank the enabled portfolios (PIT indicators, `snapshots` code path);
      2. SWITCH gates -> hypothetical proposal; acceptance policy
         'accept-after-N-weeks-confirmation' -> the shadow defender switches;
      3. REALLOCATION path mechanically (numeric scenario triggers only);
      4. costs are applied by the shadow book (`shadow_book_nav`);
      5. the shadow book NAV is recorded.

    The decision loop collects a TARGET SCHEDULE; the book is then stepped once
    over it. Both arms start from the SAME seeded defender at t=start and
    diverge ONLY because A applies the proposals.

    `switch_signal` (M6 investigation, owner-approved A/B — NOT part of the
    Task 9.1 spec, which is the 'ranking' default):
      - 'ranking' — the spec's path: trailing-Sortino ranking discovers the
        challenger, the 5 UC8-A gates accept it. Measured hit-rate ~0.50: the
        756d window is 3y of past, so the trigger is stale by construction.
      - 'regime' — the experiment: a confirmed regime flip nominates the book
        DESIGNED_FOR the new regime type (an edge the schema has carried since
        M1 and the mechanical path never read); the gates drop to VETO duty —
        absolute Calmar floor, binding caps, maturity, meaningful change. The
        rank/sortino-gap gates are deliberately absent: they ARE the stale
        discoverer this variant replaces. No extra confirmation lag either —
        a regime is already `regime_confirm_prints`-confirmed before it is
        visible (`created_at`). A regime type with no DESIGNED_FOR book
        (notably 'uncertain') nominates nobody: hold.

    SCENARIO HYSTERESIS (M6 finding — the first replay chased trigger flicker
    through 184 reallocations at a 0.474 hit-rate, the 1993-94 ping-pong): the
    scenario feeding the blend is the CONFIRMED one — a different raw winner
    (incl. back to 'base') must repeat for `confirmation_weeks` consecutive
    decision dates before it takes over. The same remedy, for the same disease,
    as the M3 regime detector's `regime_confirm_prints`: `^VIX > 25` flickers,
    and an instantaneous read makes the book chase every flicker at 10 bps a
    round trip. The knob is deliberately the SAME `replay_confirmation_weeks`
    the switch arm uses — one acceptance policy, not two. Measured: ~110
    reallocations instead of 184, ~+0.10pts/y.

    A separate post-reallocation COOLDOWN was tried here and REMOVED: measured
    at -0.03pts/y and a lower Sortino, it earned nothing the hysteresis above
    does not already do (the spec's `proposal_cooldown_weeks` is an
    anti-repetition pre-gate keyed on USER rejections — that one arrives with
    the user-decision path at M8).
    """
    calendar = _book_calendar(inputs)
    dates = decision_dates(calendar, start, end, cadence)
    initial = inputs.portfolios[inputs.initial_defender_id].allocation
    if not dates:
        return _empty_result()

    t0 = dates[0]
    targets: dict[pd.Timestamp, dict[str, float]] = {t0: dict(initial)}
    proposals: list[ShadowProposal] = []
    defender_id = inputs.initial_defender_id
    held: dict[str, float] = dict(initial)
    pending_challenger: str | None = None
    pending_count = 0
    n_switches = 0
    # Scenario hysteresis state (see the docstring). Confirmed starts at
    # 'base': the strategy's structural default, the same neutral start the
    # warm-start job assumes.
    confirmed_scenario = SCENARIO_BASE
    pending_scenario: str | None = None
    pending_scenario_count = 0

    for t in dates:
        rows = _valuation_rows_asof(inputs, defender_id, t)
        ranked = rank_portfolios(rows, thresholds.tiebreak_window)
        by_id = {rr.row.portfolio_id: rr for rr in ranked}
        defender_row = by_id[defender_id]
        mature = {
            pid
            for pid, frame in inputs.panel.items()
            if int(frame.index.searchsorted(t, side="right")) >= MIN_CANDIDACY_OBS
        }

        # -- A: switch (UC8-A) --
        accepted: str | None = None
        if switch_signal == "regime":
            # Regime-keyed variant: the nomination is already hysteresis-
            # confirmed (created_at visibility), so it takes effect the same
            # decision date — adding the ranking arm's confirmation lag would
            # double-confirm an already-confirmed signal.
            accepted = _designed_challenger(
                inputs, ranked, defender_row, t, mature, thresholds.proposal
            )
        else:
            challenger = _best_challenger(
                ranked, defender_row, inputs.caps, thresholds.proposal, mature
            )
            if challenger is None:
                pending_challenger, pending_count = None, 0
            else:
                if challenger == pending_challenger:
                    pending_count += 1
                else:
                    pending_challenger, pending_count = challenger, 1
                # 'accept-after-N-weeks-confirmation': the SAME challenger must
                # clear the gates on N consecutive decision dates before the
                # shadow defender moves — the replay's stand-in for the live
                # cycle's confirmation, and the reason a one-week ranking
                # flicker does not churn the book.
                if pending_count >= confirmation_weeks:
                    accepted = challenger
                    pending_challenger, pending_count = None, 0

        if accepted is not None:
            target = dict(inputs.portfolios[accepted].allocation)
            proposals.append(ShadowProposal(t, "switch", dict(held), target, accepted))
            targets[t] = target
            held, defender_id = target, accepted
            n_switches += 1
            # A switch changes the strategy whose scenarios apply — the
            # hysteresis state restarts at the new strategy's 'base'.
            confirmed_scenario = SCENARIO_BASE
            pending_scenario, pending_scenario_count = None, 0
            continue

        # -- scenario hysteresis step --
        strategy_id = inputs.portfolios[defender_id].primary_strategy_id
        raw = _active_scenario(inputs, strategy_id, t) if strategy_id else None
        raw_name = raw.name if raw else SCENARIO_BASE
        if raw_name == confirmed_scenario:
            pending_scenario, pending_scenario_count = None, 0
        elif raw_name == pending_scenario:
            pending_scenario_count += 1
        else:
            pending_scenario, pending_scenario_count = raw_name, 1
        if pending_scenario is not None and pending_scenario_count >= confirmation_weeks:
            confirmed_scenario = pending_scenario
            pending_scenario, pending_scenario_count = None, 0

        # -- B: reallocation (UC8-B) — only when no switch fired this week --
        proposed = _reallocation_target(
            inputs, defender_id, held, t, thresholds.proposal, confirmed_scenario
        )
        if proposed is not None:
            proposals.append(ShadowProposal(t, "reallocation", dict(held), proposed, defender_id))
            targets[t] = proposed
            held = proposed

    nav_a, turnover = shadow_book_nav(targets, inputs.prices, inputs.rf, cost_bps, calendar)
    nav_b, _ = shadow_book_nav({t0: dict(initial)}, inputs.prices, inputs.rf, cost_bps, calendar)
    resolved = _resolve_proposals(inputs, proposals, cost_bps, calendar)

    return ReplayResult(
        nav_agent_follow=nav_a,
        nav_hold_defender=nav_b,
        metrics_agent_follow=nav_metrics(nav_a, inputs.rf),
        metrics_hold_defender=nav_metrics(nav_b, inputs.rf),
        proposals=resolved,
        n_switches=n_switches,
        avg_turnover=turnover / len(proposals) if proposals else 0.0,
        hit_rate_12w=_rate(resolved, "won"),
        false_signal_rate=_rate(resolved, "lost"),
        pit_assertions_passed=pit_assertions(inputs, dates),
    )


def _best_challenger(
    ranked: Sequence[RankedRow],
    defender: RankedRow,
    caps: Caps,
    thresholds: ProposalThresholds,
    mature: set[str],
) -> str | None:
    """The highest-ranked challenger that clears ALL 5 switch gates. Walking
    the whole ranking (rather than testing rank 1 only) is what the live
    Writeback does: the gates are candidacy tests, so a rank-1 row that breaches
    a binding cap does not veto the rank-2 row behind it.

    `mature` = the portfolios past the `MIN_CANDIDACY_OBS` floor as-of t; an
    immature row stays ranked but cannot challenge (same shape as the drawdown
    rule's ranked-but-not-proposable)."""
    for rr in ranked:
        if rr.row.defender or rr.row.portfolio_id not in mature:
            continue
        if gates.switch_gates(rr, defender, caps, thresholds).passed:
            return rr.row.portfolio_id
    return None


def _designed_challenger(
    inputs: ReplayInputs,
    ranked: Sequence[RankedRow],
    defender: RankedRow,
    t: pd.Timestamp,
    mature: set[str],
    thresholds: ProposalThresholds,
) -> str | None:
    """The `switch_signal='regime'` nomination (see `run_replay`): the book
    DESIGNED_FOR the regime type KNOWN at t, subjected to the VETO gates only
    — absolute Calmar floor, binding caps, maturity, meaningful change. Walked
    in ranked order so several books designed for the same type tie-break the
    same way everything else does. No nomination (regime 'uncertain', unmapped
    type, or the designed book is already the defender) means HOLD."""
    regime_type_id = _regime_asof(inputs, t)
    if regime_type_id is None:
        return None
    for rr in ranked:
        row = rr.row
        if row.defender or row.portfolio_id not in mature:
            continue
        if row.designed_regime_type_id != regime_type_id:
            continue
        if row.calmar_rolling is None or row.calmar_rolling < thresholds.calmar_min:
            continue
        if not gates.concentration_ok(row.allocation, inputs.caps):
            continue
        if not gates.drawdown_ok(row.max_drawdown, inputs.caps):
            continue
        change = gates.max_allocation_change_pts(defender.row.allocation, row.allocation)
        if change < thresholds.min_allocation_change_pts:
            continue
        return row.portfolio_id
    return None


def _reallocation_target(
    inputs: ReplayInputs,
    defender_id: str,
    held: Mapping[str, float],
    t: pd.Timestamp,
    thresholds: ProposalThresholds,
    confirmed_scenario: str,
) -> dict[str, float] | None:
    """The mechanical reallocation path: blend the CONFIRMED scenario's
    tactical target (hysteresis lives in `run_replay`) with the FAVORS
    structural anchor, then let the UC8-B gates decide. There is no separate
    "should I propose?" trigger: the skill's own trigger ("allocation drift vs
    blend target > 5pts") IS gate 3 (`min_allocation_change_pts`, 5.0), so the
    gates alone settle it — a reallocation too small to matter is refused
    rather than paying costs.

    THE FAVORS LEG IS OWN-STRATEGY ONLY (M6 amendment to docs/ARCHITECTURE.md's
    literal "top-FAVORS strategy's prescribed allocation"): when the top-FAVORS
    strategy for the current regime is NOT the defender's own, the leg
    contributes ZERO rather than pulling the book toward another strategy's
    allocation. Blending toward a different strategy's book is a HALF-SWITCH by
    the back door — it changes strategy exposure while bypassing all 5 switch
    gates and the confirmation policy, and it un-does fresh switches (measured:
    each leg ~neutral alone, -0.55pts/y combined, the 1993-94 ping-pong).
    Changing strategy is the switch path's job; the reallocation path tunes the
    defender WITHIN its own strategy, which is also how docs/USE_CASES.md UC8-B
    frames it ("adjusting the DEFENDER's own allocation")."""
    strategy_id = inputs.portfolios[defender_id].primary_strategy_id
    if strategy_id is None:
        return None

    scenario_target = next(
        (
            s.target_allocation
            for s in inputs.scenarios
            if s.strategy_id == strategy_id and s.name == confirmed_scenario
        ),
        None,
    )

    regime_type_id = _regime_asof(inputs, t)
    favors_strategy = _favors_asof(inputs, regime_type_id, t) if regime_type_id else None
    # Own-strategy guard (see docstring). When it IS the defender's own, the
    # prescribed (base) target anchors the book back toward its structural
    # allocation — docs/EXAMPLE.md Step 8's zero-delta case falls out naturally
    # when the book already sits there.
    favors_target = (
        inputs.prescribed.get(favors_strategy) if favors_strategy == strategy_id else None
    )

    proposed = gates.blend_allocation(held, scenario_target, favors_target, thresholds)
    outcome = gates.reallocation_gates(
        held, proposed, inputs.caps, thresholds, inputs.allowed_tickers
    )
    return proposed if outcome.passed else None


def pit_assertions(inputs: ReplayInputs, dates: Sequence[pd.Timestamp]) -> bool:
    """ "zero PIT assertions failed" (docs/MILESTONES.md M6 DoV).

    These check what is NOT already guaranteed by construction. Asserting
    "no row with ts > t was read" would be a TAUTOLOGY — every read filters on
    `<= t`, so the assertion could never fail and would certify nothing. The
    real leak risk is in the DATA's own dating, so that is what is checked
    here; the complementary behavioural proof (a future-dated row cannot move
    an earlier decision) is `test_replay_point_in_time`, where it belongs.

    1. `created_at >= start_date` for every regime. A confirmation cannot
       precede the regime it confirms. If `created_at` were ever back-dated to
       the data, the `regime_confirm_prints` hysteresis window would leak — the
       exact few weeks of look-ahead docs/TASKS.md Task 9.1 names.
    2. Some regime is visible by the last decision date. Catches the opposite
       regression: a `created_at` stamped at WALL-CLOCK (the seed's own "now")
       rather than at the confirming print, which would silently make the
       regime and FAVORS legs dead for the entire replay instead of failing."""
    if any(r.created_at < r.start_date for r in inputs.regimes):
        return False
    return not dates or any(r.created_at <= dates[-1] for r in inputs.regimes)


def _resolve_proposals(
    inputs: ReplayInputs,
    proposals: list[ShadowProposal],
    cost_bps: float,
    calendar: pd.DatetimeIndex,
) -> list[ShadowProposal]:
    """Proposal verdicts at +12w (docs/USE_CASES.md UC8-C, the metric the live
    scoreboard continues): synthetic NAV of the PROPOSED allocation vs the
    INCUMBENT one over the following `proposal_outcome_weeks`, net of the
    switch cost the proposal itself would incur. A proposal whose 12-week
    window runs past `end` stays unresolved (verdict None) rather than being
    scored on a truncated window."""
    horizon = int(ratios.TRADING_DAYS_PER_WEEK * 12)
    resolved: list[ShadowProposal] = []
    for p in proposals:
        forward = calendar[calendar >= p.date][: horizon + 1]
        if len(forward) <= horizon:
            resolved.append(p)
            continue
        proposed_nav, _ = shadow_book_nav(
            {p.date: p.to_allocation}, inputs.prices, inputs.rf, cost_bps, forward
        )
        incumbent_nav, _ = shadow_book_nav(
            {p.date: p.from_allocation}, inputs.prices, inputs.rf, 0.0, forward
        )
        if proposed_nav.empty or incumbent_nav.empty:
            resolved.append(p)
            continue
        # The proposed leg pays the switch cost it would really incur; the
        # incumbent leg trades nothing, so it pays none.
        cost = _switch_cost(p.from_allocation, p.to_allocation, cost_bps)
        proposed_return = float(proposed_nav.iloc[-1] / proposed_nav.iloc[0] - 1.0) - cost
        incumbent_return = float(incumbent_nav.iloc[-1] / incumbent_nav.iloc[0] - 1.0)
        verdict = "won" if proposed_return > incumbent_return else "lost"
        resolved.append(dataclasses.replace(p, verdict=verdict))
    return resolved


def _switch_cost(
    from_allocation: Mapping[str, float], to_allocation: Mapping[str, float], cost_bps: float
) -> float:
    a, b = _fractions(from_allocation), _fractions(to_allocation)
    delta_sum = sum(abs(b.get(t, 0.0) - a.get(t, 0.0)) for t in set(a) | set(b))
    return delta_sum * cost_bps / 10_000.0


def _rate(proposals: Sequence[ShadowProposal], verdict: str) -> float | None:
    scored = [p for p in proposals if p.verdict is not None]
    if not scored:
        return None
    return sum(1 for p in scored if p.verdict == verdict) / len(scored)


def compute_context(inputs: ReplayInputs, result: ReplayResult) -> ReplayContext | None:
    """Build the `ReplayContext` arms on the replay's own calendar, zero-cost
    statics (a static book trades nothing beyond its monthly rebalance, which
    the cost model deliberately does not charge)."""
    calendar = _book_calendar(inputs)
    mdd_a = result.metrics_agent_follow.max_drawdown
    if mdd_a is None or len(calendar) < 2:
        return None

    def static(allocation: Mapping[str, float]) -> NavMetrics:
        nav, _ = shadow_book_nav(
            {pd.Timestamp(calendar[0]): dict(allocation)}, inputs.prices, inputs.rf, 0.0, calendar
        )
        return nav_metrics(nav, inputs.rf)

    statics = {pid: static(meta.allocation) for pid, meta in inputs.portfolios.items()}
    measurable = {p: m for p, m in statics.items() if m.max_drawdown is not None}
    if not measurable:
        return None
    pole_id = max(measurable, key=lambda p: cast("float", measurable[p].max_drawdown))

    initial = inputs.portfolios[inputs.initial_defender_id].allocation
    pole = inputs.portfolios[pole_id].allocation
    tickers = set(initial) | set(pole)
    best_weight, best_metrics, best_gap = 0.0, statics[inputs.initial_defender_id], float("inf")
    for step in range(21):
        w = step / 20.0
        blend = {x: (1 - w) * initial.get(x, 0.0) + w * pole.get(x, 0.0) for x in tickers}
        metrics = static(blend)
        if metrics.max_drawdown is None:
            continue
        gap = abs(metrics.max_drawdown - mdd_a)
        if gap < best_gap:
            best_weight, best_metrics, best_gap = w, metrics, gap

    qualifying = {
        p: m
        for p, m in measurable.items()
        if cast("float", m.max_drawdown) >= mdd_a and m.cagr is not None
    }
    best_id = max(qualifying, key=lambda p: cast("float", qualifying[p].cagr), default=None)

    return ReplayContext(
        defensive_pole_id=pole_id,
        matched_risk_weight=best_weight,
        static_matched_risk=best_metrics,
        static_best_id=best_id,
        static_best=qualifying[best_id] if best_id else None,
    )


def _book_calendar(inputs: ReplayInputs) -> pd.DatetimeIndex:
    """The trading calendar the shadow book steps on: the initial defender's
    own NAV index — i.e. exactly the dates `ratios.synthesize_nav` produced for
    that allocation (every constituent priced). Both arms share it, so A - B
    can never be an artefact of a calendar difference."""
    frame = inputs.panel.get(inputs.initial_defender_id)
    return pd.DatetimeIndex([]) if frame is None else pd.DatetimeIndex(frame.index)


def _empty_result() -> ReplayResult:
    empty = pd.Series(dtype=float)
    none_metrics = NavMetrics(None, None, None, None)
    return ReplayResult(empty, empty, none_metrics, none_metrics, [], 0, 0.0, None, None, True)


# -- async DB layer --------------------------------------------------------


async def load_inputs(db: InvestmentDB) -> ReplayInputs:
    """Read the whole PIT-filterable world ONCE (docs/TASKS.md Task 9.4: the
    replay is "READ-ONLY on the live DB ... it never commits fake events/
    vertices to the live graph (that would poison history)")."""
    portfolios = await _load_portfolios(db)
    defender_id = next((p for p, m in portfolios.items() if m.defender), None)
    if defender_id is None:
        raise ValueError("replay: no defender portfolio — UC0 seed must run first")

    panel = {pid: await _load_panel(db, pid) for pid in portfolios}
    tickers = {t for m in portfolios.values() for t in m.allocation if t != ratios.CASH_TICKER}
    scenarios = await _load_scenarios(db)
    tickers |= {t for s in scenarios for t in s.target_allocation if t != ratios.CASH_TICKER}
    prices = {t: await ratios.load_price(db, t) for t in sorted(tickers)}

    caps_rows = await db.query(
        "SELECT max_single_asset_pct, max_drawdown_pct FROM user_profile LIMIT 1"
    )
    if not caps_rows:
        raise ValueError("replay: no user_profile — the binding caps are not optional")

    allowed = await db.query("SELECT ticker FROM allowed_tickers WHERE active = 1")

    return ReplayInputs(
        panel=panel,
        portfolios=portfolios,
        prices={t: p for t, p in prices.items() if not p.empty},
        rf=await ratios.load_rf_daily(db),
        regimes=await _load_regimes(db),
        backtests=await _load_backtests(db),
        scenarios=scenarios,
        prescribed=_prescribed_allocations(scenarios),
        caps=Caps(
            max_single_asset_pct=float(caps_rows[0]["max_single_asset_pct"]),
            max_drawdown_pct=float(caps_rows[0]["max_drawdown_pct"]),
        ),
        # The synthetic 'cash' sleeve has no allowed_tickers row (it accrues at
        # rf_daily rather than being fetched — db/seed_data.py) but is a legal
        # sleeve of every seeded portfolio.
        allowed_tickers=frozenset({str(r["ticker"]) for r in allowed} | {ratios.CASH_TICKER}),
        initial_defender_id=defender_id,
    )


async def _load_portfolios(db: InvestmentDB) -> dict[str, PortfolioMeta]:
    rows = await db.query(
        "SELECT portfolio.id, portfolio.defender, portfolio.framework_id, portfolio.allocation, "
        "(SELECT regime_type_id FROM designed_for "
        " WHERE designed_for.portfolio_id = portfolio.id LIMIT 1) AS designed_regime_type_id, "
        "(SELECT strategy_id FROM holds "
        " WHERE holds.portfolio_id = portfolio.id AND holds.is_primary = 1 LIMIT 1) "
        " AS primary_strategy_id "
        "FROM portfolio WHERE enabled = 1 ORDER BY portfolio.id"
    )
    return {
        str(r["id"]): PortfolioMeta(
            portfolio_id=str(r["id"]),
            defender=bool(r["defender"]),
            framework_id=str(r["framework_id"]),
            designed_regime_type_id=r["designed_regime_type_id"],
            primary_strategy_id=r["primary_strategy_id"],
            allocation=json.loads(r["allocation"]),
        )
        for r in rows
    }


async def _load_panel(db: InvestmentDB, portfolio_id: str) -> pd.DataFrame:
    rows = await db.query(
        "SELECT ts, sharpe_rolling, sortino_rolling, calmar_rolling, drawdown "
        "FROM portfolio_nav WHERE portfolio_id = :pid ORDER BY ts",
        pid=portfolio_id,
    )
    if not rows:
        return pd.DataFrame()
    columns = ("sharpe_rolling", "sortino_rolling", "calmar_rolling", "drawdown")
    return pd.DataFrame(
        [{k: r[k] for k in columns} for r in rows],
        index=pd.DatetimeIndex([r["ts"] for r in rows]),
    )


async def _load_regimes(db: InvestmentDB) -> list[RegimeInstance]:
    """Closed instances only — an ongoing regime is not a completed period, and
    FAVORS aggregates over completed ones (`backtests._completed_regimes`)."""
    rows = await db.query(
        "SELECT id, regime_type_id, start_date, end_date, created_at FROM regime "
        "WHERE end_date IS NOT NULL ORDER BY start_date, id"
    )
    return [
        RegimeInstance(
            regime_id=str(r["id"]),
            regime_type_id=str(r["regime_type_id"]),
            start_date=pd.Timestamp(str(r["start_date"])),
            end_date=pd.Timestamp(str(r["end_date"])),
            created_at=pd.Timestamp(str(r["created_at"])),
        )
        for r in rows
    ]


async def _load_backtests(db: InvestmentDB) -> list[BacktestRow]:
    rows = await db.query(
        "SELECT strategy_id, regime_id, sortino_rolling FROM backtest ORDER BY id"
    )
    return [
        BacktestRow(
            strategy_id=str(r["strategy_id"]),
            regime_id=str(r["regime_id"]),
            sortino_rolling=ratios.flt(r["sortino_rolling"]),
        )
        for r in rows
    ]


async def _load_scenarios(db: InvestmentDB) -> list[ScenarioMeta]:
    """Each scenario's numeric trigger DISJUNCTION, evaluated over the full
    daily calendar once — `mechanical/scenarios.py` owns the grammar, so the
    replay reads triggers exactly as the warm-start job scored them."""
    rows = await db.query(
        "SELECT id, strategy_id, name, target_allocation, triggers FROM scenario "
        "ORDER BY strategy_id, id"
    )
    parsed: dict[str, list[Conjunction]] = {}
    needed: set[str] = set()
    for r in rows:
        disjuncts: list[Conjunction] = []
        for trigger in json.loads(r["triggers"]) if r["triggers"] else []:
            conjunction = parse_trigger_conjunction(trigger)
            if conjunction is not None:
                disjuncts.append(conjunction)
        parsed[str(r["id"])] = disjuncts
        needed.update(t for c in disjuncts for t, _, _ in c)

    levels = {t: await _signal_level(db, t) for t in sorted(needed)}
    non_empty = [s for s in levels.values() if not s.empty]
    calendar = (
        pd.date_range(
            min(s.index.min() for s in non_empty), max(s.index.max() for s in non_empty), freq="D"
        )
        if non_empty
        else pd.DatetimeIndex([])
    )
    aligned = {t: s.reindex(calendar).ffill() for t, s in levels.items()}

    return [
        ScenarioMeta(
            scenario_id=str(r["id"]),
            strategy_id=str(r["strategy_id"]),
            name=str(r["name"]),
            target_allocation=json.loads(r["target_allocation"]),
            active=(
                evaluate_trigger_series(parsed[str(r["id"])], aligned)
                if parsed[str(r["id"])]
                else pd.Series(dtype=bool)
            ),
        )
        for r in rows
    ]


async def _signal_level(db: InvestmentDB, ticker: str) -> pd.Series:
    rows = await db.query(
        "SELECT ts, level FROM market_data WHERE ticker = :t AND level IS NOT NULL ORDER BY ts",
        t=ticker,
    )
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        [r["level"] for r in rows], index=pd.DatetimeIndex([r["ts"] for r in rows]), dtype=float
    )


def _prescribed_allocations(scenarios: Sequence[ScenarioMeta]) -> dict[str, dict[str, float]]:
    """ "Prescribed allocation of a strategy = its base-scenario
    `target_allocation` (structural); bull/bear scenario targets are tactical
    variants" (docs/ARCHITECTURE.md)."""
    return {s.strategy_id: s.target_allocation for s in scenarios if s.name == SCENARIO_BASE}


async def load_thresholds(db: InvestmentDB) -> ReplayThresholds:
    """The CURRENT `system_thresholds` — the set the replay runs unless the
    caller overrides it (Task 9.2's grid search does)."""
    rows = await db.query("SELECT key, value FROM system_thresholds")
    values = {str(r["key"]): float(r["value"]) for r in rows}
    return ReplayThresholds(
        proposal=ProposalThresholds(
            sortino_gap_min=values["proposal_sortino_gap_min"],
            calmar_min=values["proposal_calmar_min"],
            min_allocation_change_pts=values["proposal_min_allocation_change_pts"],
            max_turnover_pct=values["proposal_max_turnover_pct"],
            blend_scenario_weight=values["blend_scenario_weight"],
            blend_favors_weight=values["blend_favors_weight"],
        ),
        tiebreak_window=values["ranking_tiebreak_window"],
    )


def thresholds_map(thresholds: ReplayThresholds) -> dict[str, float]:
    """The `replay_report.thresholds` MAP — "the set replayed"
    (docs/DATA_MODELS.md), keyed by the `system_thresholds` names so a report
    can be read against the live config without a translation table."""
    return {
        "proposal_sortino_gap_min": thresholds.proposal.sortino_gap_min,
        "proposal_calmar_min": thresholds.proposal.calmar_min,
        "proposal_min_allocation_change_pts": thresholds.proposal.min_allocation_change_pts,
        "proposal_max_turnover_pct": thresholds.proposal.max_turnover_pct,
        "ranking_tiebreak_window": thresholds.tiebreak_window,
        "blend_scenario_weight": thresholds.proposal.blend_scenario_weight,
        "blend_favors_weight": thresholds.proposal.blend_favors_weight,
    }


# The report's own honest-bounds label — docs/TASKS.md Task 9.1 SCOPE, kept
# WITH the numbers rather than in a doc, because these are the numbers people
# quote.
NOTES = (
    "Mechanical mode (kind='mechanical'), the AUTOMATED go-live evidence: PIT by "
    "construction (first-release vintages, ADR-003; regimes visible on created_at; "
    "FAVORS re-aggregated as-of t). CONSERVATIVE APPROXIMATION: the reallocation path "
    "runs numeric scenario triggers only — the Worker's qualitative judgment is not "
    "simulated (its marginal contribution is Task 9.4 / M8b). SCOPE: the portfolio "
    "universe is FIXED (no forward discovery) and the quality of the 7 seeded "
    "portfolios is half of what this measures; invariant weights are not in the "
    "mechanical path. Costs are TRADE FEES ONLY — taxes out of scope (assume a "
    "tax-advantaged wrapper). After an accepted reallocation the shadow book's "
    "holdings differ from the defender vertex's seeded allocation, which the ranking "
    "still reads (Task 9.1 step 1 ranks the enabled portfolios)."
)


async def replay(
    db: InvestmentDB,
    start: date,
    end: date,
    *,
    thresholds: ReplayThresholds | None = None,
    cadence: str = "weekly",
    persist: bool = True,
) -> ReplayResult:
    """Task 9.1 — the mechanical live chain, accelerated, for every Monday in
    [start, end], persisted as a `replay_report` row + a ReplayEvent.

    M8b (Task 9.4) adds `include_worker: bool` to THIS function — the agentic
    mode is the same harness with the Planner/Worker call inserted before the
    same gates, never a second decision loop. It is not a parameter yet
    because no Worker exists to switch on (CLAUDE.md "no speculative stubs").
    """
    inputs = await load_inputs(db)
    thresholds = thresholds or await load_thresholds(db)
    rows = await db.query("SELECT key, value FROM system_thresholds")
    values = {str(r["key"]): float(r["value"]) for r in rows}
    cost_bps = values["replay_cost_bps"]
    confirmation_weeks = values["replay_confirmation_weeks"]

    result = run_replay(
        inputs,
        thresholds,
        start=start,
        end=end,
        cost_bps=cost_bps,
        confirmation_weeks=confirmation_weeks,
        cadence=cadence,
    )
    result = dataclasses.replace(result, context=compute_context(inputs, result))
    if persist:
        await _persist_report(db, result, thresholds, start, end, cost_bps, confirmation_weeks)
    return result


async def _persist_report(
    db: InvestmentDB,
    result: ReplayResult,
    thresholds: ReplayThresholds,
    start: date,
    end: date,
    cost_bps: float,
    confirmation_weeks: float,
) -> str:
    """The ReplayEvent is appended BEFORE the `replay_report` row, in the same
    transaction (CLAUDE.md "EventLog" rule)."""
    report_id = str(ULID())
    policy = ACCEPTANCE_POLICY.format(weeks=confirmation_weeks)
    async with db.transaction():
        await db.append_event(
            type="ReplayEvent",
            source_uc="system",
            source_id=report_id,
            payload={
                "kind": KIND_MECHANICAL,
                "window": [start.isoformat(), end.isoformat()],
                "agent_follow": result.metrics_agent_follow.as_map(),
                "hold_defender": result.metrics_hold_defender.as_map(),
                "n_switches": result.n_switches,
                "hit_rate_12w": result.hit_rate_12w,
                "pit_assertions_passed": result.pit_assertions_passed,
                # Context arms (ReplayContext) — informational, never the gate.
                "context": (
                    None
                    if result.context is None
                    else {
                        "defensive_pole_id": result.context.defensive_pole_id,
                        "matched_risk_weight": result.context.matched_risk_weight,
                        "static_matched_risk": result.context.static_matched_risk.as_map(),
                        "static_best_id": result.context.static_best_id,
                        "static_best": (
                            result.context.static_best.as_map()
                            if result.context.static_best
                            else None
                        ),
                    }
                ),
            },
            event_date=end,
        )
        await db.command(
            "INSERT INTO replay_report (id, run_at, window_start, window_end, kind, thresholds, "
            " acceptance_policy, nav_agent_follow, nav_hold_defender, n_switches, avg_turnover, "
            " hit_rate_12w, false_signal_rate, cost_bps, pit_assertions_passed, vintage_mode, "
            " delta_vs_mechanical, behavioral_log, notes) "
            "VALUES (:id, :run_at, :ws, :we, :kind, :thresholds, :policy, :nav_a, :nav_b, "
            " :n_switches, :turnover, :hit, :false_rate, :cost, :pit, :vintage, NULL, NULL, "
            " :notes)",
            id=report_id,
            run_at=pd.Timestamp.utcnow().isoformat(),
            ws=start.isoformat(),
            we=end.isoformat(),
            kind=KIND_MECHANICAL,
            thresholds=json.dumps(thresholds_map(thresholds)),
            policy=policy,
            nav_a=json.dumps(result.metrics_agent_follow.as_map()),
            nav_b=json.dumps(result.metrics_hold_defender.as_map()),
            n_switches=result.n_switches,
            turnover=result.avg_turnover,
            hit=result.hit_rate_12w,
            false_rate=result.false_signal_rate,
            cost=cost_bps,
            pit=result.pit_assertions_passed,
            vintage=VINTAGE_FIRST_RELEASE,
            notes=NOTES,
        )
    return report_id


# -- runner (docs/TASKS.md Task 9.1 "Done when": `python -m
# investment.mechanical.replay` produces a full 35y report) ----------------

# The proxy floor: the earliest tradable date the HISTORY_PROXIES splice
# reaches (docs/TASKS.md Task 9.1 "START DEFAULTS TO THE EARLIEST TRADABLE
# DATE (~1991, the proxy floor)").
DEFAULT_START = date(1991, 1, 1)


def render(result: ReplayResult, start: date, end: date) -> str:
    """The owner's M6 instrument (docs/MILESTONES.md: "Each milestone ships its
    own inspection view"). Reports the two arms side by side on EVERY metric,
    not just the headline: the M6 verdict is "does adapting pay", and a core
    that trades CAGR for drawdown is a different answer than one that simply
    loses."""
    a, b = result.metrics_agent_follow, result.metrics_hold_defender
    edge = (a.cagr or 0.0) - (b.cagr or 0.0)
    lines = [
        f"35y shadow replay  {start} -> {end}   (kind={KIND_MECHANICAL}, "
        f"vintage={VINTAGE_FIRST_RELEASE})",
        "",
        f"{'':22s}{'A agent-follow':>16s}{'B hold-defender':>18s}",
        f"{'CAGR':22s}{_pct(a.cagr):>16s}{_pct(b.cagr):>18s}",
        f"{'Sortino':22s}{_num(a.sortino):>16s}{_num(b.sortino):>18s}",
        f"{'Calmar':22s}{_num(a.calmar):>16s}{_num(b.calmar):>18s}",
        f"{'Max drawdown':22s}{_pct(a.max_drawdown):>16s}{_pct(b.max_drawdown):>18s}",
        "",
        f"edge (A - B, CAGR)    {edge * 100:+.3f} pts/y",
        f"switches              {result.n_switches}",
        f"proposals             {len(result.proposals)} "
        f"(switch {sum(1 for p in result.proposals if p.kind == 'switch')}, "
        f"reallocation {sum(1 for p in result.proposals if p.kind == 'reallocation')})",
        f"avg turnover          {result.avg_turnover:.3f} per proposal",
        f"hit-rate at +12w      {_pct(result.hit_rate_12w)}",
        f"false-signal rate     {_pct(result.false_signal_rate)}",
        f"PIT assertions        {'PASSED' if result.pit_assertions_passed else 'FAILED'}",
    ]
    if result.context is not None:
        c = result.context
        m = c.static_matched_risk
        lines += [
            "",
            "context arms (adaptation vs naive static de-risking — informational, not the gate):",
            f"  matched-risk static   {int((1 - c.matched_risk_weight) * 100)}/"
            f"{int(c.matched_risk_weight * 100)} defender/{c.defensive_pole_id}: "
            f"CAGR {_pct(m.cagr)}, Sortino {_num(m.sortino)}, mdd {_pct(m.max_drawdown)}",
        ]
        if c.static_best_id is not None and c.static_best is not None:
            b = c.static_best
            lines.append(
                f"  best in-menu static   {c.static_best_id} (in-sample pick): "
                f"CAGR {_pct(b.cagr)}, Sortino {_num(b.sortino)}, mdd {_pct(b.max_drawdown)}"
            )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 9 shadow replay (Task 9.1, mechanical mode) — the M6 premise gate."
    )
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--cadence", choices=("weekly", "monthly"), default="weekly")
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="run without writing the replay_report row + ReplayEvent",
    )
    args = parser.parse_args()

    async def run() -> None:
        # pydantic-settings populates required fields from .env at runtime;
        # mypy can't see that (same inline ignore as `seed.main`).
        db = InvestmentDB(Settings().db_path)  # type: ignore[call-arg]
        try:
            result = await replay(
                db,
                args.start,
                args.end,
                cadence=args.cadence,
                persist=not args.no_persist,
            )
            print(render(result, args.start, args.end))
        finally:
            await db.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
