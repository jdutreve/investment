"""The market-signal monthly stack — V1's ADOPTED allocation (ADR-007).

A countercyclical, market-priced strategy after Verdad/Rasmussen (the origin of
the approach; docs/V1_STRATEGY.md carries the attribution). Named neutrally here.

The strategy the pivot adopted (docs/V1_STRATEGY.md): a market-priced,
CONTEMPORANEOUS regime read (credit spread + yield slope, no CPI/GDP lag) picks
one of three CONCENTRATED books, and a 200-day trend-following overlay redirects
the equity/gold sleeves to intermediate Treasuries when they are below trend.
Decision cadence is MONTHLY (docs/V1_STRATEGY.md "Why monthly").

ANTI-DRIFT (the point of this module): the numbers that earned the pivot — 9.85%
CAGR / -24% daily max drawdown, +2.5 vs B, robust in AND out of sample — came
out of a scratchpad backtest (`global_table_daily.py`, the "signal+trend"
line). This is that logic ported verbatim onto the SAME NAV engine the backtest
used (`replay.shadow_book_nav`, itself pinned equal to the M4-validated
`ratios.synthesize_nav`), so wiring the stack cannot silently diverge from the
figures ADR-007 was signed on.

Those figures have been superseded TWICE, both times deliberately and
owner-arbitrated:
- 2026-08-01, `CONFIRM_DECISIONS` hysteresis: 9.85% -> 11.26% CAGR, Sortino
  0.94 -> 1.11, drawdown unchanged;
- 2026-08-02, ADR-010's single cost rate: 11.26% -> **11.14%**, Sortino 1.09,
  drawdown still -23.8%. Two disagreeing guesses were replaced by one measured
  rate: the stack had been charged 20 bps PER SIDE while `replay_cost_bps` said
  10 and the spec claimed "20 bps/rotation", and every static book it is ranked
  against paid nothing at all. Now Saxo's real 23 bps/order (no FX — every
  portfolio is USD in a USD account) bills all of them, drift-rebalance
  included.

- 2026-08-07, the OVERLAY COMPLETION below (IWN trend-checked, and the haven
  trend-checked too): 11.10% -> **10.71%** CAGR, Sortino 1.09 -> **1.17**,
  drawdown -23.78% -> **-20.61%**, turnover 42.0 -> 61.1. Measured as a 4-way
  A/B in one process on one data vintage, which is the only way to compare
  against a moving baseline (I-48):

      A  current rule            11.10%   1.09   -23.78%   turnover 42.0
      B  + IWN trend-checked     10.79%   1.17   -23.33%            53.4
      C  + haven trend-checked   11.03%   1.09   -23.78%            48.2
      D  both (adopted)          10.71%   1.17   -20.61%            61.1

  C ALONE DOES NOTHING and B alone barely moves the drawdown; together they cut
  it by 3.2 points. The interaction is the point: adding IWN is what sends large
  weight into the haven, and only then does checking the haven matter. Adopted
  against the acceptance test the proposing Worker wrote itself — "adopt only if
  it does not degrade Sortino and improves maxDD" — which D passes on both legs,
  for 0.39pp of CAGR and 45% more turnover (already charged at ADR-010's 23 bps).
  It also buys real headroom against the binding -25% cap the stack was sitting
  1.2 points inside.

- 2026-08-11, THE TWO SPREAD-TRAJECTORY KNOBS turned on at 0.20 (owner
  signature; ADR-007 is amended, not bypassed — this is the git gate ADR-006
  explicitly does not reach): 10.72% -> **11.22%** CAGR, Sortino 1.17 ->
  **1.27**, Calmar 0.52 -> 0.54, drawdown unchanged at -20.61%, turnover 61.1 ->
  67.7.

  Both came out of the Worker's most repeated critique — six wordings across
  independent M8b dates saying the book is selected on the spread's LEVEL and
  should read its TRAJECTORY. `SPREAD_SPEED_VETO` defers the risk-on book while
  the spread is still widening; `SPREAD_STRESS_SLEEVE_GATE` empties that book's
  equity sleeves on the same condition. Each adopts alone on the full sample AND
  on both halves of it, and together they are additive (+0.098 Sortino against
  +0.071 and +0.052 apart; on 1991-2008, +0.230 Sortino, +0.97pp CAGR and
  +2.96pp of drawdown).

  A THIRD mechanism from the same theme — enter the risk-on book on speed alone
  — measured nil or unstable and is NOT on. It stays in the code as
  `SPREAD_SPEED_WIDE_TRIGGER = None`, so its rejection is reproducible by a
  command rather than by a rewrite.

  25 of the 418 monthly decisions change, all in credit-stress years
  (docs/V1_STRATEGY.md has two worked examples).

The pinned pair is therefore **11.22% / -20.61%**. The earlier figures are
history, not targets. Any OTHER divergence from 10.71% is drift and must be
explained, which is what this module exists to guarantee.

ONE STANDING EXPLANATION IS ALREADY KNOWN, and naming it here is what keeps the
sentence above honest (docs/IMPROVEMENTS.md I-48): the guarantee assumes
immutable inputs over a fixed window, and neither holds. The seed's backfill
start is `today - 35y` — ROLLING — while `market_signal_cycle.HISTORY_START` is
fixed at 1991-01-01, and Yahoo restates adjusted closes retroactively. The
2026-08-03 re-seed measured the effect: 11.14% -> 11.10%, drawdown unchanged,
with 418 identical decisions — the ground moving under a fixed marker, not
drift in the logic. Treat a divergence under ~0.1pp as that; anything larger
still has to be explained.

PURE decision logic (`classify_regime`, `apply_trend_overlay`,
`advance_hysteresis`, `walk_decisions`) takes already-loaded series and holds no
I/O — the same separation as `mechanical/gates.py`, so the classifier is
unit-testable without a DB. `run_market_signal` is the thin I/O driver.

`walk_decisions` is the SINGLE decision clock: it emits one `Decision` per
monthly decision date, and BOTH consumers derive from it — the replay via
`build_targets` (which keeps only the change points `shadow_book_nav` wants) and
the LIVE monthly path (`market_signal_cycle.py`) via the last entry of the same
walk run with `end=today`. That is how the live path "calls the identical
function the replay validates": not a shared helper, the shared WALK. A live
decision that disagreed with the backtest would have to disagree with itself.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import date

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.market.derivatives import compute_derivatives
from investment.mechanical import ratios, replay
from investment.mechanical.gates import Caps, concentration_ok, drawdown_ok
from investment.mechanical.replay import NavMetrics, nav_metrics, shadow_book_nav

# The 3 books (docs/V1_STRATEGY.md). Concentrated tilts: the 50% sleeves are the
# measured source of the +2.5-vs-B edge and the reason the single-asset cap was
# raised 40 -> 50 (ADR-007 addendum). Weights are allocation percent points.
#
# NAMED AFTER THE SIGNAL STATE THAT SELECTS THEM, not after a macro regime
# (renamed 2026-07-20, ADR-007 addendum; previously growth/inflation/slowdown).
# The old names asserted a macro reading the books do not have: measured over
# the 418 monthly decisions, the market signal is essentially ORTHOGONAL to CPI
# — each book spent 28-33% of its time with CPI YoY above 3% against a 31.3%
# base rate, and the book then called "inflation" averaged CPI 2.99 vs 2.23 for
# the one called "growth" (docs/IMPROVEMENTS.md I-39). Since the Worker is an
# LLM that reads these keys as semantic context, a book called "inflation" that
# does not track inflation is a reasoning hazard, not just untidy naming.
BOOKS: dict[str, dict[str, float]] = {
    "credit-spread-wide": {"SPY": 50.0, "IWN": 40.0, "GLD": 10.0},
    "credit-spread-tight-yield-curve-flat": {"SPY": 50.0, "GLD": 40.0, "IWN": 10.0},
    "credit-spread-tight-yield-curve-steep": {"VCIT": 50.0, "IEF": 40.0, "IWN": 10.0},
}

# The 200d trend overlay: these sleeves are redirected to the haven when their
# own price is below their 200-day moving average. This is the drawdown control
# (-24% with it, -50% without — docs/V1_STRATEGY.md).
#
# IWN JOINED 2026-08-07. It is 40% of the credit-spread-wide book and was held
# at full weight whatever its own trend did — the M8b Worker found this in both
# independent runs, five times in total, and it is verifiable here: a rule whose
# whole premise is that credit is impaired kept maximum exposure to the most
# credit-sensitive equity sleeve there is. Every RISKY sleeve is now checked;
# the haven is handled separately below.
TREND_SLEEVES: tuple[str, ...] = ("SPY", "GLD", "IWN")

# The EQUITY members of the checked set — the sleeves credit stress transmits to
# first. GLD is trend-checked but is not equity, and the distinction is what the
# sleeve gate below acts on.
#
# Listed rather than read from `allowed_tickers.asset_class` because this module
# is pure decision logic with no DB (the same reason `TREND_SLEEVES` is a
# constant), and pinned to the catalog by a test so the two cannot drift.
EQUITY_SLEEVES: tuple[str, ...] = ("SPY", "IWN")

# THE HAVEN IS TREND-CHECKED TOO, and that is the other half of the same fix.
#
# The Worker's sharpest line of the 21 M8b readings, 2022-02-01, raised in BOTH
# runs on that same date: "the overlay trends the sleeve it exits but not the
# sleeve it enters". A rule that flees a falling asset into a falling asset is
# not a drawdown control. In February 2022 it moved 40% into IEF while IEF was
# below its own 200d line, in the worst bond tape of the 35-year sample.
#
# ITS OWN PROPOSAL WAS TO GATE THE HAVEN ON CPI. Refused: the stack reads
# PRICES ONLY (no macro regime, no policy, no positioning), and coupling it to
# the inflation print would reintroduce exactly the macro dependency ADR-007
# removed. The market-priced expression of the same insight is symmetry — apply
# to the destination the test already applied to the origin. When the haven is
# itself below trend, the redirect goes to cash, which cannot fall.
TREND_HAVEN = "IEF"
TREND_FALLBACK_HAVEN = ratios.CASH_TICKER

# BOTH DESTINATIONS OF THE HAVEN CHAIN ARE EXEMPT FROM THE SINGLE-ASSET CAP
# (owner, 2026-08-08), defined once because two call sites enforce it and their
# docstrings promise each other they use "the same exemption".
#
# The ADR-007 addendum exempted IEF, and that was the whole chain at the time.
# When the haven became trend-checked with a cash fallback (2026-08-07), the
# exemption did not follow it, and the flight to safety became unreachable in
# exactly the tape that needs it: measured on the M8b run of 2026-08-08, four of
# the seven inflation-shock dates (2022-03-01, -05-02, -06-01, -07-01) had all
# four sleeves AND the haven below trend, produced the 100%-cash target, and had
# it refused by the 50% cap. The stack sat in its stale book through the 2022
# drawdown.
#
# ADR-009 had already reasoned this out for the DRAWDOWN leg — refusing a
# proposal cannot exit a position, only freeze one, and the proposal blocked
# during a drawdown IS the overlay's flight to safety — and scoped that leg out
# of this path. The argument transfers wholesale to the concentration leg: cash
# at 100% is not a conviction bet, it is the absence of one, and it is the only
# destination left when every checked instrument is falling.
HAVEN_EXEMPT = frozenset({TREND_HAVEN, TREND_FALLBACK_HAVEN})

# The STACK itself as a Portfolio vertex — the object that is actually held, as
# opposed to the 3 books, which are only ever held conditionally and always
# through the overlay. It exists so the stack has a `portfolio_nav` series like
# every other portfolio: without one, its 36M rolling drawdown (the measure the
# -25% cap is about) cannot be computed at all, and the ranking compares three
# static fictions nobody holds instead of the one thing that is (ADR-009).
STACK_PORTFOLIO_ID = "ms-stack"

# Decision key -> the seeded Portfolio vertex that IS that book (db/seed_data.py
# PORTFOLIOS). The live path needs it because `Proposal.defender_id` is a
# portfolio id, not a signal state. The entity ids keep their original
# growth/inflation/slowdown spelling: they already appear in committed EventLog
# payloads, which are append-only (seed_data's ms-growth-book note). This map is
# the ONE place the frozen spelling meets the renamed decision keys, so no other
# module has to know both vocabularies.
BOOK_PORTFOLIO_IDS: dict[str, str] = {
    "credit-spread-wide": "ms-growth-book",
    "credit-spread-tight-yield-curve-flat": "ms-inflation-book",
    "credit-spread-tight-yield-curve-steep": "ms-slowdown-book",
}

# The market-signal series and their trailing-median lookbacks. ~10y median
# (2520 trading days) with a 1y warm-up floor, matching the backtest.
CREDIT_SPREAD = "BAA10Y"
YIELD_SLOPE = "T10Y2Y"
MEDIAN_WINDOW_DAYS = 2520
MEDIAN_MIN_DAYS = 252
MA_WINDOW_DAYS = 200

# Every ticker any book can hold — what `run_market_signal` must load prices for. The
# bug that once crippled this stack (docs/STRATEGY_COMPARISON.md correction note)
# was loading a prices dict MISSING IWN/VCIT, which then held flat at 0%; naming
# the set here makes that omission impossible to repeat silently.
STACK_TICKERS: tuple[str, ...] = ("SPY", "IWN", "GLD", "VCIT", "IEF")

# The stack is charged at the SAME per-order rate as every other NAV in the
# system (ADR-010): Saxo's real 23 bps. Was 20 here — which happened to be
# double `replay_cost_bps` AND double the "20 bps/rotation" the spec claimed,
# while every static book it is ranked against paid nothing.
COST_BPS = ratios.TRADING_COST_BPS

# Consecutive monthly decisions that must name the SAME new book before the
# stack switches (measured 2026-08-01, full 35y + split sample).
#
# `classify_regime` is a bare comparison against a trailing median, so a signal
# hovering at its own median flips the book on an arbitrarily small difference:
# of the 36 book changes over 409 monthly decisions, 25% were decided by a
# margin under 2% and 14 reversed within 3 months. Books barely overlap (wide is
# SPY/IWN/GLD, steep is VCIT/IEF/IWN), so such a flip is close to a 90% round
# trip. Waiting for confirmation lifts CAGR 9.85% -> 11.26% and Sortino
# 0.94 -> 1.11 at an UNCHANGED -23.8% drawdown, in both halves of the history
# split independently; the sweep degrades past ~4, so this is a real optimum,
# not "trade less" (buy-and-hold scores 10.32% CAGR but -52% drawdown).
#
# 3 is `regime_confirm_prints`' value, deliberately: one hysteresis convention
# across the project. A separate constant, NOT a read of that threshold —
# recalibrating the macro detector must never silently move the allocation.
# Holding the two candidate books during the wait (the literal "intersection"
# proposal) measured WORSE than simply waiting, and raised turnover.
CONFIRM_DECISIONS = 3


def describe_rule() -> str:
    """The stack's rule as prompt text, GENERATED FROM THE CONSTANTS ABOVE.

    The Worker is asked to challenge this rule, which means it has to know what
    the rule IS. Until now nothing told it: its context carried the month's
    DECISION (book, weights, which sleeves were below trend) but never the
    mechanism, so it reasoned about the rule from whatever it recalled.

    It recalled wrong. Across the two M8b runs it twice described the overlay as
    covering "SPY only" while `TREND_SLEEVES` includes GLD — and its own run's
    logs printed `below-trend=['SPY','GLD']` on the very dates it said so. A
    third reading stated it correctly. Sound critiques, unreliable descriptions
    of the status quo, and no way for it to tell which it was doing.

    Generated rather than written out, for the same reason `describe_schema`
    reads the tables from SQLite: a hand-copied rule is wrong at the first edit
    and wrong SILENTLY, which is the exact failure this repairs. Change
    `TREND_SLEEVES` and this text changes with it.

    That promise was only half kept, and it broke the same day. The sleeve list
    and haven name interpolated, but the SENTENCE around them was hand-written
    and said the overlay redirects below-trend sleeves to IEF, full stop — six
    hours after the haven itself became trend-checked with a cash fallback. The
    Worker read it, believed a 100% IEF book was still reachable, and spent an
    innovation proposing the fallback that already existed. Every knob the rule
    turns on is now interpolated: sleeves, haven, fallback, both windows and the
    hysteresis count. Nothing about the mechanism is asserted in prose that a
    constant could state instead — the test below asserts exactly that.

    It does NOT breach the Worker's unawareness of Planner/Writeback/storage
    (worker/agent.py): the stack is an INVESTMENT instrument whose output the
    Worker already reads and is invited to challenge. Telling it how the
    instrument works is telling it about the market, not about the plumbing."""
    books = "\n".join(
        f"    {name}: " + ", ".join(f"{t} {w:.0f}" for t, w in holdings.items())
        for name, holdings in BOOKS.items()
    )
    # The checked set is the sleeves PLUS the haven — `walk_decisions` computes
    # `below_trend` over exactly this set, and the haven's own read is what
    # selects TREND_FALLBACK_HAVEN. Deriving it here rather than naming the
    # sleeves alone is what keeps this text true when the overlay changes.
    checked = (*TREND_SLEEVES, TREND_HAVEN)
    return (
        "THE MECHANICAL RULE THAT DECIDED THIS MONTH (market-signal stack)\n"
        f"  1. Credit spread (BAA10Y) vs its {MEDIAN_WINDOW_DAYS // 252}-year trailing\n"
        "     median, and yield slope (T10Y2Y) vs its own, select ONE of three books:\n"
        f"{books}\n"
        f"  2. A book change is applied only after {CONFIRM_DECISIONS} consecutive\n"
        "     monthly decisions agree (hysteresis against boundary flip-flop).\n"
        f"  3. {MA_WINDOW_DAYS}-day trend overlay: {', '.join(checked)} — and ONLY\n"
        f"     these — are checked against their own {MA_WINDOW_DAYS}d moving average.\n"
        f"     Whichever sleeve is below is redirected to {TREND_HAVEN}; and when\n"
        f"     {TREND_HAVEN} is ITSELF below its own line, the destination becomes\n"
        f"     {TREND_FALLBACK_HAVEN} instead — including the {TREND_HAVEN} a book\n"
        "     already holds. Sleeves outside the checked set are held at book\n"
        "     weight whatever their own trend does.\n"
        "  The rule reads PRICES only: no macro regime, no policy, no positioning."
    )


@dataclasses.dataclass(frozen=True)
class TrendRead:
    """One trend sleeve's 200d overlay read at a decision date. Carries the two
    numbers the comparison was made on, not just its boolean answer: "SPY is
    below trend" is unauditable, "SPY 512.40 vs MA200 548.10" is."""

    price: float
    moving_average: float | None  # None before MA_WINDOW_DAYS of history
    below: bool
    # WHY it is below, when the price alone does not say so. A sleeve redirected
    # by `SPREAD_STRESS_SLEEVE_GATE` reads `below=True` with a price ABOVE its
    # moving average, and a reader comparing the two numbers would call that a
    # bug. Recording the cause is the same discipline as the digest line that
    # said "redirected to IEF" while the target was cash (fixed 2026-08-08): a
    # journal that contradicts the decision is worse than no journal.
    credit_gated: bool = False


@dataclasses.dataclass(frozen=True)
class Decision:
    """ONE monthly decision, with everything that produced it.

    This is the audit record the live path persists (and the replay could): the
    raw signal state, the book actually HELD after hysteresis, the sleeves below
    their 200d MA, and the post-overlay target. `changed` is True iff the target
    differs from the previous decision's — i.e. iff this decision moves money.

    Medians are `None` during warm-up (before MEDIAN_MIN_DAYS of history), which
    is the state `classify_regime` answers with the credit-spread-wide default;
    keeping it None rather than NaN is what lets the whole record serialise
    straight into a Proposal's `market_context` as valid JSON."""

    date: pd.Timestamp
    signalled: str  # what the signal said THIS decision, before hysteresis
    held: str  # the book actually in force after hysteresis
    pending: str | None  # a different book waiting for confirmation
    pending_count: int  # consecutive decisions it has been waiting
    spread: float
    spread_median: float | None
    slope: float
    slope_median: float | None
    trend: dict[str, TrendRead]  # per TREND_SLEEVES sleeve
    target: dict[str, float]  # post-overlay effective allocation
    changed: bool

    @property
    def below_trend(self) -> tuple[str, ...]:
        """The sleeves the overlay redirected to TREND_HAVEN this decision."""
        return tuple(t for t, read in self.trend.items() if read.below)


@dataclasses.dataclass(frozen=True)
class MarketSignalRun:
    """A backtest/replay of the stack over a window.

    `targets` maps each CHANGE date -> the book that took effect (only dates
    where the allocation actually changed, matching `shadow_book_nav`'s
    time-varying target contract); `turnover` is its summed round-trip turnover.
    `decisions` is the FULL journal — every decision date, changed or not — which
    is what the live path needs: a decision that lands on the held book still
    advances the hysteresis counter and still has to be recorded.
    `raw_series` keeps each input UN-forward-filled, so the live path can report
    the date every input became knowable (ADR-003)."""

    nav: pd.Series
    targets: dict[pd.Timestamp, dict[str, float]]
    turnover: float
    decisions: list[Decision] = dataclasses.field(default_factory=list)
    raw_series: dict[str, pd.Series] = dataclasses.field(default_factory=dict)


# -- pure decision logic (no I/O — unit-testable, shared with the live path) --


# THE WORKER'S MOST REPEATED CRITIQUE, made measurable (2026-08-09). OFF by
# default: `None` leaves `classify_regime` exactly as ADR-007 validated it, and
# `rule_revision` can switch it on to earn a 35-year verdict before anything is
# adopted.
#
# Six distinct formulations across independent dates and runs said one thing:
# the book is selected on the LEVEL of the spread against its trailing median,
# and should be selected on its TRAJECTORY. At 2020-03-02 — "BAA10Y 2.27 vs
# median 2.59 says 'tight'. The level is tight; the TRAJECTORY is not. 2008
# taught exactly this lesson: the level-vs-median read stays 'tight' longest
# precisely when widening is fastest, because the median trails and the level
# starts low."
#
# THE DIRECTION IS THE OPPOSITE OF WHAT "VETO" SUGGESTS, and getting it backwards
# would measure the reverse of the claim. `credit-spread-wide` is the RISK-ON
# book (SPY 50 / IWN 40 / GLD 10): the countercyclical bet that stress is already
# priced. The Worker's objection is to taking that bet while the stress is still
# forming — "the spread is wide because a credit event is still forming"
# (2008-07-01). So the veto DEFERS the wide reading while the spread is still
# widening faster than the threshold, falling through to the curve branch and
# its lighter book. It does not accelerate into it.
#
# Units are the spread's own (percentage points of BAA10Y) over
# SPREAD_SPEED_LOOKBACK_DAYS.
SPREAD_SPEED_VETO: float | None = 0.20

# THE SAME THEME'S OTHER MECHANISM, and the two are not the same claim.
#
# Reading the six wordings of the velocity critique showed at least three
# distinct proposals inside one theme (2026-08-11): defer the wide book while
# the spread widens (the veto above), ENTER it on speed regardless of level
# (this), and redirect the equity sleeves on spread direction without waiting
# for the 200d (not expressible yet). Grouping them was right; treating them as
# interchangeable was not.
#
# This is the countercyclical bet taken EARLIER: "when BAA10Y speed and
# acceleration are both strongly positive, treat the credit regime as
# spread-wide regardless of the level-vs-median" (Worker, verbatim, with its own
# candidate of +0.20). The premise is ADR-007's own — stress that is priced
# precedes strong forward returns — so a spread gapping out is the signal
# arriving before the level catches up, and the 200d overlay still guards the
# downside.
#
# PRECEDENCE, since the two knobs pull opposite ways: the trigger is an ENTRY on
# speed and is read first; the veto questions the LEVEL-based read and applies
# only to it. Setting both is coherent but measures a rule nobody proposed, so
# they are swept separately.
SPREAD_SPEED_WIDE_TRIGGER: float | None = None

# MECHANISM (c) OF THE VELOCITY THEME, and the one four separate critiques asked
# for: "gate the credit-spread-wide book's equity sleeves on spread DIRECTION,
# not only on the 200d price trend", "credit-regime gate on the IWN sleeve",
# "credit-contagion gate on the small-cap sleeve".
#
# The complaint underneath all four: the book is SELECTED because credit is
# impaired, and it then holds 90% equities — the most credit-sensitive exposure
# there is — with nothing but each sleeve's own price trend between the stack
# and that bet. The 200d reads one price at a time and cannot carry the
# cross-signal that chose the book.
#
# Verbatim (Worker, 2026-08): "When BAA10Y is above its trailing median AND its
# trailing speed is positive, treat the selected book's equity sleeves as
# below-trend — redirect them to the haven — regardless of price vs the 200d."
# Both conditions, as proposed: a wide LEVEL and a widening TRAJECTORY. The
# proposal said a 3-month speed and this uses the 30-day series the rule already
# computes, which is a deviation worth knowing when reading the verdict.
#
# Distinct from the veto, which defers the whole BOOK: this keeps the book and
# empties its equity, so the two are separable hypotheses and are swept apart.
SPREAD_STRESS_SLEEVE_GATE: float | None = 0.20

# Matches `system_thresholds.derivative_lookback_short`, and the speed itself is
# computed by `market.derivatives.compute_derivatives` rather than differenced
# here — CLAUDE.md's "two implementations must produce the same numbers" applies
# to a knob that will be compared against readings the Worker saw.
SPREAD_SPEED_LOOKBACK_DAYS = 30


def classify_regime(
    spread: float,
    spread_median: float | None,
    slope: float,
    slope_median: float | None,
    spread_speed: float | None = None,
) -> str:
    """The market-signal regime (docs/V1_STRATEGY.md "Regime signal"):
    credit spread WIDE vs its 10y median -> `credit-spread-wide` (stress is
    PRICED, so the countercyclical response is to buy risk); else, on the
    slope: FLAT vs its 10y median -> `credit-spread-tight-yield-curve-flat`,
    STEEP -> `credit-spread-tight-yield-curve-steep`.

    The returned key names the SIGNAL STATE, not a macro regime — see BOOKS.
    Note the deliberate asymmetry the names encode: when the spread is WIDE the
    yield curve is never consulted, which is why that key carries no curve term.

    A missing median (warm-up, before MEDIAN_MIN_DAYS of history) defaults to
    `credit-spread-wide` — the equity-tilted book — exactly as the backtest did
    rather than stalling; the trend overlay still guards its downside."""
    fast = (
        SPREAD_SPEED_WIDE_TRIGGER is not None
        and spread_speed is not None
        and not pd.isna(spread_speed)
        and spread_speed > SPREAD_SPEED_WIDE_TRIGGER
    )
    if fast:
        return "credit-spread-wide"
    wide = spread_median is None or pd.isna(spread_median) or spread > spread_median
    # See SPREAD_SPEED_VETO: defer the risk-on book while the crack is still
    # opening. A missing speed (warm-up) never vetoes — the rule falls back to
    # the level read it has always used.
    if wide and SPREAD_SPEED_VETO is not None and spread_speed is not None:
        wide = pd.isna(spread_speed) or spread_speed <= SPREAD_SPEED_VETO
    if wide:
        return "credit-spread-wide"
    if slope_median is None or pd.isna(slope_median) or slope < slope_median:
        return "credit-spread-tight-yield-curve-flat"
    return "credit-spread-tight-yield-curve-steep"


def apply_trend_overlay(book: Mapping[str, float], below_trend: frozenset[str]) -> dict[str, float]:
    """Redirect each TREND_SLEEVES weight to the haven when that sleeve is below
    its 200d MA. Weights merge additively — if a book already holds the haven
    (the credit-spread-tight-yield-curve-steep book holds IEF), a redirected
    sleeve adds to it.

    THE HAVEN IS CHOSEN BY THE SAME TEST it applies to everything else: when
    TREND_HAVEN is itself in `below_trend`, the destination becomes
    TREND_FALLBACK_HAVEN. `below_trend` therefore carries the haven's own read
    as well as the sleeves' — see `walk_decisions`, which computes it for
    `TREND_SLEEVES + (TREND_HAVEN,)`.

    A book that HOLDS the haven as a sleeve (steep: IEF 40) also has that weight
    moved when the haven is below trend — it is the same asset failing the same
    test, and leaving it in place while refusing to redirect INTO it would be
    incoherent."""
    haven = TREND_FALLBACK_HAVEN if TREND_HAVEN in below_trend else TREND_HAVEN
    adjusted: dict[str, float] = {}
    for ticker, weight in book.items():
        redirected = ticker in below_trend and ticker in (*TREND_SLEEVES, TREND_HAVEN)
        destination = haven if redirected else ticker
        adjusted[destination] = adjusted.get(destination, 0.0) + float(weight)
    return adjusted


def advance_hysteresis(
    held: str | None, pending: str | None, pending_count: int, signalled: str
) -> tuple[str, str | None, int]:
    """One step of the `CONFIRM_DECISIONS` hysteresis: the stack stays in the
    book it is committed to until a DIFFERENT book has been named that many
    decisions in a row. The first decision commits immediately (`held is None` —
    there is nothing to hold yet), and a candidate that flickers back resets the
    count. Returns the state AFTER the step: `(held, pending, pending_count)`.

    Pulled out of the walk so the state machine is one testable function rather
    than a loop body — and so the live path can state its carried-over state in
    the same three names it reads back from the previous decision."""
    if held is None or signalled == held:
        return signalled, None, 0
    count = pending_count + 1 if pending == signalled else 1
    if count >= CONFIRM_DECISIONS:
        return signalled, None, 0
    return held, signalled, count


def walk_decisions(
    dates: Sequence[pd.Timestamp],
    spread: pd.Series,
    slope: pd.Series,
    spread_median: pd.Series,
    slope_median: pd.Series,
    moving_averages: Mapping[str, pd.Series],
    prices: Mapping[str, pd.Series],
    spread_speed: pd.Series | None = None,
) -> list[Decision]:
    """Walk the decision clock and record EVERY decision — the full journal.

    The trend overlay is NOT damped: it re-reads the 200d MA on every decision,
    so the drawdown control keeps reacting while a book switch waits out its
    confirmation window."""
    decisions: list[Decision] = []
    previous: dict[str, float] | None = None
    held: str | None = None
    pending: str | None = None
    pending_count = 0
    for t in dates:
        signalled = classify_regime(
            _at(spread, t),
            _at(spread_median, t),
            _at(slope, t),
            _at(slope_median, t),
            None if spread_speed is None else _at(spread_speed, t),
        )
        held, pending, pending_count = advance_hysteresis(held, pending, pending_count, signalled)
        # THE HAVEN IS READ TOO, not only the sleeves: `apply_trend_overlay`
        # needs its own trend to decide whether redirecting INTO it is still
        # sound (see that function). It is reported in `trend` alongside the
        # sleeves, so the digest and the Worker see the same read the rule used.
        trend = {
            ticker: _trend_read(_at(prices[ticker], t), _at(moving_averages[ticker], t))
            for ticker in (*TREND_SLEEVES, TREND_HAVEN)
            if ticker in prices and ticker in moving_averages
        }
        # THE CREDIT GATE, applied to the READS rather than beside them, so the
        # journalled trend and the book that follows from it cannot disagree.
        if SPREAD_STRESS_SLEEVE_GATE is not None and spread_speed is not None:
            median = _at(spread_median, t)
            speed = _at(spread_speed, t)
            stressed = (
                not pd.isna(median)
                and _at(spread, t) > median
                and not pd.isna(speed)
                and speed > SPREAD_STRESS_SLEEVE_GATE
            )
            if stressed:
                trend = {
                    ticker: (
                        dataclasses.replace(read, below=True, credit_gated=not read.below)
                        if ticker in EQUITY_SLEEVES
                        else read
                    )
                    for ticker, read in trend.items()
                }
        # `ticker`, not `t` — `t` is the decision date in this scope, and while
        # a generator expression has its own, reusing the name here reads as a
        # shadow to anyone auditing the walk.
        book = apply_trend_overlay(
            BOOKS[held], frozenset(ticker for ticker, read in trend.items() if read.below)
        )
        decisions.append(
            Decision(
                date=t,
                signalled=signalled,
                held=held,
                pending=pending,
                pending_count=pending_count,
                spread=_at(spread, t),
                spread_median=_opt(_at(spread_median, t)),
                slope=_at(slope, t),
                slope_median=_opt(_at(slope_median, t)),
                trend=trend,
                target=book,
                changed=book != previous,
            )
        )
        previous = book
    return decisions


def build_targets(
    dates: Sequence[pd.Timestamp],
    spread: pd.Series,
    slope: pd.Series,
    spread_median: pd.Series,
    slope_median: pd.Series,
    moving_averages: Mapping[str, pd.Series],
    prices: Mapping[str, pd.Series],
) -> dict[pd.Timestamp, dict[str, float]]:
    """The change-point map `shadow_book_nav` consumes: a target ONLY on the
    dates the book actually changes (a monthly re-evaluation that lands on the
    same book pays no turnover). A pure projection of `walk_decisions` — the
    replay and the live path cannot drift because there is only one walk."""
    decisions = walk_decisions(
        dates, spread, slope, spread_median, slope_median, moving_averages, prices
    )
    return {d.date: d.target for d in decisions if d.changed}


def _at(series: pd.Series, t: pd.Timestamp) -> float:
    """Point read that tolerates a decision date off the series index (returns
    NaN), so `classify_regime`'s warm-up default fires instead of a KeyError."""
    value = series.get(t)
    return float("nan") if value is None else float(value)


def _trend_read(price: float, moving_average: float) -> TrendRead:
    """One sleeve's overlay read. A missing MA (warm-up) is NOT below trend —
    the overlay stays out of the way until it has 200 days to speak with, the
    same "unmeasured is not bad" rule `gates.drawdown_ok` applies."""
    ma = _opt(moving_average)
    return TrendRead(price=price, moving_average=ma, below=ma is not None and price < ma)


def _opt(value: float) -> float | None:
    """NaN -> None, so a warm-up median serialises as JSON `null` rather than
    the bare token `NaN`, which `json.loads` accepts but no other reader does."""
    return None if pd.isna(value) else value


# -- gate confrontation (the caps still BIND the adopted stack — CLAUDE.md) ---


def cap_violations(run: MarketSignalRun, caps: Caps, stack_drawdown: float | None) -> list[str]:
    """The binding-cap confrontation M6-bis's DoV asserts is empty. Every target
    book must clear the single-asset cap (now 50) EXCEPT the trend-haven sleeve,
    and the STACK's realized drawdown must clear the drawdown cap (now -25%,
    applied to the stack, not to each book standalone — ADR-007). Returns the
    failing gate names, [] if none.

    The haven CHAIN is exempted from the single-asset cap (`HAVEN_EXEMPT`:
    ADR-007 addendum choice (a) for IEF, extended to the cash fallback by the
    owner on 2026-08-08): the overlay's flight-to-safety can pile both
    equity/gold sleeves into IEF (~90% in risk-off) and, when IEF is itself
    below trend, all of it into cash — the deliberate drawdown control, not a
    conviction bet. Uses the SAME `gates.py` predicate the live Writeback runs,
    with the same exemption, so a book blocked live was blocked here too.

    A BUILD-TIME check over a whole backtest, deliberately not the live gate:
    the live path's equivalent (`writeback.market_signal_gates`) sees one
    decision, this sees every target the run ever held, and it is the drawdown
    leg that separates them — here it is the WHOLE-WINDOW figure the DoV
    asserts, whereas live the rule is a 36-month rolling ALERT and never blocks
    (ADR-009). Called by the M6-bis validation and by `test_market_signal.py`."""
    violations: list[str] = []
    for t, book in sorted(run.targets.items()):
        if not concentration_ok(book, caps, exempt=HAVEN_EXEMPT):
            violations.append(f"max_single_asset_pct@{t.date()}")
    if not drawdown_ok(stack_drawdown, caps):
        violations.append("max_drawdown_pct@stack")
    return violations


# -- I/O driver -------------------------------------------------------------


async def run_market_signal(
    db: InvestmentDB,
    *,
    start: date = date(1991, 1, 1),
    end: date = date(2026, 7, 1),
    cadence: str = "monthly",
    cost_bps: float = COST_BPS,
) -> MarketSignalRun:
    """Load the series, run the pure logic, price it on the shared NAV engine.
    Defaults reproduce ADR-007's backtest window and MONTHLY cadence."""
    inputs = await replay.load_inputs(db)
    calendar = replay._book_calendar(inputs)
    rf = await ratios.load_rf_daily(db)

    prices = {t: await ratios.load_price(db, t) for t in STACK_TICKERS}
    prices = {t: p for t, p in prices.items() if not p.empty}
    missing = set(STACK_TICKERS) - set(prices)
    if missing:
        # The exact failure the correction note warns about — refuse to run a
        # stack silently missing a sleeve rather than hold it flat at 0%.
        raise ValueError(f"market-signal stack missing price series for {sorted(missing)}")

    # Keep the RAW series alongside the calendar-aligned one: the ffill that
    # carries a stale print forward is right for the decision (it is what was
    # knowable) but destroys the publication date, and ADR-003 vintage discipline
    # is only auditable if the live path can say WHEN each input became knowable.
    spread_raw = await ratios.load_price(db, CREDIT_SPREAD)
    slope_raw = await ratios.load_price(db, YIELD_SLOPE)
    absent = [t for t, s in ((CREDIT_SPREAD, spread_raw), (YIELD_SLOPE, slope_raw)) if s.empty]
    if absent:
        # The same refusal as the missing sleeve above, for a WORSE failure mode.
        # An absent signal series reindexes to all-NaN, `classify_regime` reads
        # that as warm-up, and the stack silently holds `credit-spread-wide` —
        # the 90%-equity book — on no signal at all. It is not a warm-up: the
        # decision would be uninformed rather than early, and nothing downstream
        # could tell the two apart (`knowable_at` is None in both cases).
        # `seed._missing_stack_series` pre-checks the same two series to SKIP
        # rather than raise (the incremental-seed contract); the LIVE cycle has
        # no such pre-check, and this is what stops it deciding blind.
        #
        # Only ABSENT is caught here, and deliberately: a series that STOPPED
        # updating ffills and still decides, which `alerts.signal_freshness_alert`
        # reports rather than blocks. An alert and never a block is the owner's
        # recorded decision (docs/MILESTONES.md, second coherence pass
        # 2026-08-02) — ADR-003 says a stale print IS what was knowable, and
        # ADR-009 scopes the live path to telling rather than refusing.
        raise ValueError(f"market-signal stack missing signal series for {absent}")
    spread = spread_raw.reindex(calendar).ffill()
    slope = slope_raw.reindex(calendar).ffill()
    spread_median = spread.rolling(MEDIAN_WINDOW_DAYS, min_periods=MEDIAN_MIN_DAYS).median()
    slope_median = slope.rolling(MEDIAN_WINDOW_DAYS, min_periods=MEDIAN_MIN_DAYS).median()
    moving_averages = {
        ticker: prices[ticker].rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()
        for ticker in (*TREND_SLEEVES, TREND_HAVEN)
    }

    dates = replay.decision_dates(calendar, start, end, cadence)
    # Computed unconditionally and cheap: the walk ignores it while
    # SPREAD_SPEED_VETO is None, and computing it only when the knob is set
    # would make the measured variant read a series the baseline never built.
    spread_speed = compute_derivatives(spread, CREDIT_SPREAD, SPREAD_SPEED_LOOKBACK_DAYS)["speed"]
    decisions = walk_decisions(
        dates, spread, slope, spread_median, slope_median, moving_averages, prices, spread_speed
    )
    targets = {d.date: d.target for d in decisions if d.changed}
    nav, turnover = shadow_book_nav(targets, prices, rf, cost_bps, calendar)
    return MarketSignalRun(
        nav=nav,
        targets=targets,
        turnover=turnover,
        decisions=decisions,
        raw_series={CREDIT_SPREAD: spread_raw, YIELD_SLOPE: slope_raw, **prices},
    )


async def persist_stack_nav(
    db: InvestmentDB, run: MarketSignalRun, window: int
) -> ratios.NavBackfillResult:
    """Write the stack's daily NAV to `portfolio_nav` under STACK_PORTFOLIO_ID.

    A PAPER SERIES, and every reader of it must know so. `shadow_book_nav`
    prices the DECISION WALK: it assumes each monthly target was executed at the
    close of its anchor date, with no slippage, no partial fill and no delay
    between the digest landing and the owner placing the order. V1 executes
    nothing (ADR-006), so no realized series exists to compare it to; this is
    what the STRATEGY would have done, and it is legitimate to rank it against
    portfolios measured the same way — but it is not a statement about the
    owner's account, and the digest and the drawdown alert both say so. Closing
    that gap is forward paper-mode (docs/V1_STRATEGY.md Step 6). One consequence
    worth naming: the walk includes the current month's target even if
    `market_signal_gates` then refused it, so a blocked decision leaves the NAV
    a month ahead of the position. Reachable only through a code or config
    change (ADR-009), which is a bug being surfaced, not a market event.

    The series is `run.nav` — the one `shadow_book_nav` already produced, which
    follows the book through every switch AND every overlay redirect. Persisting
    it is what makes the stack measurable at all: `ratios.value_portfolios`, the
    UC7 ranking and the digest all read `portfolio_nav`, so once this row exists
    the stack's 36M rolling drawdown, Sortino and Calmar arrive through exactly
    the same formulas as every portfolio it is compared against.

    Rebuilt in full on each run rather than appended: the series is DERIVED from
    market data and the pure walk, so recomputing is both cheap and the only way
    a late-arriving price vintage can correct history it should have been in
    (`append_ts_batch` is INSERT OR REPLACE, so this is idempotent).

    The Portfolio vertex's `allocation` is deliberately NOT touched here. That
    column records what the stack HOLDS, which changes only when a decision is
    committed — so it is written inside `writeback.dispose_market_signal`'s
    transaction, after its EventLog append (CLAUDE.md "EventLog"). Writing it
    here would move held state on a week that decided nothing."""
    return await ratios.persist_nav(db, STACK_PORTFOLIO_ID, run.nav.dropna(), window)


async def stack_metrics(db: InvestmentDB, run: MarketSignalRun) -> NavMetrics:
    """Daily NAV metrics of the run (CAGR, Sortino, max drawdown) — the numbers
    the DoV checks against the pinned pair **11.14% / -23.8%** (see the module's
    ANTI-DRIFT note). 11.26% was the pre-ADR-010 figure and 9.85% the
    pre-hysteresis one; both are history, and naming a superseded target here is
    how a drift check comes to certify the wrong number."""
    rf = await ratios.load_rf_daily(db)
    return nav_metrics(run.nav.dropna(), rf)
