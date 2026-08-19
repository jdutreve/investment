"""The ANTI-DRIFT check (mechanical/market_signal.py `drift_violations` /
`check_drift`; the alert in mechanical/alerts.py).

WHAT THIS FILE EXISTS FOR. ADR-007's guarantee is that the wired stack still
reproduces the numbers it was signed on. Until 2026-08-12 nothing enforced it:
the pinned pair lived in a docstring paragraph, no test read it, no CLI command
ran it, no chain step checked it — the whole guarantee was a human remembering
to open a REPL and compare against prose. It failed exactly as an unread number
fails: that paragraph told the reader to explain any divergence from 10.71%
while the bold pair two lines above it said 11.57%, two supersessions later.

TWO LAYERS, because they answer different questions and only one of them needs a
35-year database:

  - the RULE (`drift_violations`) is pure over an already-measured `NavMetrics`,
    so the tolerance, the sign handling and the missing-indicator case are
    checked on synthetic numbers in microseconds, hermetically, like every other
    test here;
  - the MEASUREMENT (`check_drift` against the live DB) is the DoV itself, and
    it can only run where the 35 years are. It SKIPS with a reason when they are
    not — the doctrine tests/conftest.py already applies to the embedding model:
    "a skipped test says 'unverified here'; a stubbed one says 'verified' and
    lies". A synthetic 35-year history would be the stub.
"""

import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as MS
from investment.mechanical.replay import NavMetrics

# The owner's live database (.env `DB_PATH`, documented in .env.example). Read
# from the environment first so the check follows a relocated database rather
# than silently skipping beside it.
#
# EXPANDED THE WAY `config.ExpandedPath` EXPANDS IT, and the first version was
# not: `.env` ships `DB_PATH=$HOME/data/...`, so a literal `Path(os.environ[...])`
# is the string `$HOME/data/...`, which exists nowhere — and this test SKIPPED on
# the one machine that has the database. A skip for the wrong reason is worse
# than no test: it reads as "unverified here" on the only host where it could
# ever have run.
LIVE_DB = Path(
    os.path.expandvars(os.environ.get("DB_PATH", ""))
    or (Path.home() / "data" / "investment" / "investment.db")
).expanduser()

_SKIP_REASON = (
    f"the live database ({LIVE_DB}) is not present — the anti-drift DoV needs the real 35-year "
    "history and is UNVERIFIED here, not passing. See this module's docstring."
)


def _metrics(cagr: float, max_drawdown: float) -> NavMetrics:
    """A measured pair, with the two indicators the check does not gate left
    None — `drift_violations` must ignore them rather than crash on them."""
    return NavMetrics(cagr=cagr, sortino=None, calmar=None, max_drawdown=max_drawdown)


def test_the_pinned_pair_itself_does_not_drift() -> None:
    """The identity case, and the one that catches a fat-fingered constant."""
    assert MS.drift_violations(_metrics(MS.PINNED_CAGR, MS.PINNED_MAX_DRAWDOWN)) == []


def test_movement_inside_the_tolerance_is_the_ground_and_not_drift() -> None:
    """I-48: the backfill start rolls and Yahoo restates adjusted closes, so the
    indicators move a little with 418 IDENTICAL decisions. An exact comparison
    would cry drift every re-seed and be switched off within a month."""
    inside = MS.DRIFT_TOLERANCE_PP / 100.0 * 0.9
    assert MS.drift_violations(_metrics(MS.PINNED_CAGR + inside, MS.PINNED_MAX_DRAWDOWN)) == []
    assert MS.drift_violations(_metrics(MS.PINNED_CAGR - inside, MS.PINNED_MAX_DRAWDOWN)) == []


def test_drift_is_reported_in_both_directions_with_both_numbers() -> None:
    """A BETTER number is drift too, and this is not pedantry: the stack getting
    faster on its own is exactly what an accidental rule change looks like, and
    the 2026-08-11 methodology error (an unbounded window) showed up first as an
    improvement. The message carries both arms and the gap, because "cagr" alone
    sends the reader back to the REPL this check replaces."""
    outside = MS.DRIFT_TOLERANCE_PP / 100.0 * 2.0
    better = MS.drift_violations(_metrics(MS.PINNED_CAGR + outside, MS.PINNED_MAX_DRAWDOWN))
    worse = MS.drift_violations(_metrics(MS.PINNED_CAGR - outside, MS.PINNED_MAX_DRAWDOWN))
    assert len(better) == 1 and len(worse) == 1
    for message in (*better, *worse):
        assert "cagr" in message
        assert f"{MS.PINNED_CAGR * 100:.2f}%" in message  # the pinned arm
        assert "pp, tolerance" in message


def test_a_deeper_drawdown_is_drift_too() -> None:
    """The drawdown is a NEGATIVE fraction, so the comparison has to be on the
    absolute gap and not on an ordering — the sign trap `NavMetrics.deltas`
    documents from the other side."""
    outside = MS.DRIFT_TOLERANCE_PP / 100.0 * 2.0
    violations = MS.drift_violations(_metrics(MS.PINNED_CAGR, MS.PINNED_MAX_DRAWDOWN - outside))
    assert len(violations) == 1 and "max_drawdown" in violations[0]


def test_an_unmeasured_indicator_is_not_drift() -> None:
    """ "Unmeasured is not bad" — the same rule `gates.drawdown_ok` applies. A
    window too short to produce a CAGR must not be reported as a strategy that
    changed."""
    assert MS.drift_violations(NavMetrics(None, None, None, None)) == []


def test_the_pinned_window_is_what_run_market_signal_defaults_to() -> None:
    """One constant, not three. `PINNED_WINDOW` is the window every figure in
    the module's ANTI-DRIFT note was measured over, `run_market_signal`'s own
    default, and `rule_revision.FULL_WINDOW` — which existed only to name "the
    window run_market_signal defaults to" and could do it only by copying the
    literals."""
    import inspect

    from investment.mechanical.rule_revision import FULL_WINDOW

    signature = inspect.signature(MS.run_market_signal)
    assert signature.parameters["start"].default == MS.PINNED_WINDOW[0]
    assert signature.parameters["end"].default == MS.PINNED_WINDOW[1]
    assert FULL_WINDOW == MS.PINNED_WINDOW


@pytest.mark.skipif(not LIVE_DB.exists(), reason=_SKIP_REASON)
async def test_the_live_stack_still_reproduces_its_pinned_pair(tmp_path: Path) -> None:
    """THE DoV, executed (docs/MILESTONES.md M6-bis: "replay-validate the wired
    stack reproduces the pinned numbers — the anti-drift check that caught the
    M6 rebalance-order bug").

    Runs against a COPY. `InvestmentDB.__init__` applies `ADDED_COLUMNS` with
    ALTER TABLE, so opening the owner's live database would be a WRITE from a
    test — and ADR-004 makes the running agent its sole writer. The copy costs
    ~0.4s against a 0.6s check and removes the question entirely.

    A FAILURE HERE IS NOT NECESSARILY A BUG. It means one of two things, and
    they need opposite responses: a rule moved without `PINNED_CAGR` /
    `PINNED_MAX_DRAWDOWN` being re-signed in the same commit (fix the code, or
    sign the new pair — that is the git gate ADR-006 does not reach), or the
    ground moved under a fixed marker (I-48). The message prints both arms so
    the reader can tell which conversation to have."""
    copy = tmp_path / "live-copy.db"
    shutil.copy(LIVE_DB, copy)
    db = InvestmentDB(copy)

    check = await MS.check_drift(db)
    assert check.measurable, f"the live database could not answer the DoV: {check.reason}"
    assert check.violations == [], (
        f"the wired stack no longer reproduces ADR-007's pinned pair: {'; '.join(check.violations)}"
    )


@pytest.mark.skipif(not LIVE_DB.exists(), reason=_SKIP_REASON)
async def test_the_stack_still_beats_its_own_control_arm(tmp_path: Path) -> None:
    """THE ATTRIBUTION, enforced (2026-08-13; docs/V1_STRATEGY.md).

    ADR-007 was signed on +3.80pp/yr against All Weather — a PASSIVE benchmark,
    which answers "better than holding a static book" and not the question that
    decides whether the signal layer is worth its complexity: better than the
    SAME 300d overlay on a book that never rotates? Measured, the signal's whole
    marginal contribution is +0.24pp of CAGR and +0.20 of Sortino, against the
    overlay's 30 points of drawdown. The margin is real and it is small, which
    is exactly why it needs a machine watching it rather than a paragraph.

    TWO ASSERTIONS, and they are deliberately structural rather than pinned to
    the measured deltas. The numbers move with the ground (I-48) and the margin
    is only ~2.5x the noise floor, so pinning them would produce a test that
    fails on a re-seed. What must not change is the SHAPE of the result:

      - the two arms share a max drawdown, because the covid trough that sets it
        happened while the frozen book was in force — the mechanical form of
        `rule_revision.adopt`'s finding that the -20.61% belongs to the overlay
        and not to book selection;
      - the stack's Sortino EXCEEDS the control's. If that ever stops being
        true, the signal layer has stopped paying for itself and the owner needs
        to hear it from a red test, not from a study someone might re-run.

    Both arms are priced from ONE `load_series` on one vintage, in one process —
    the I-48 discipline `RevisionMeasurement` already applies to baseline vs
    variant, for the same reason: a delta between two loads is not a delta."""
    copy = tmp_path / "live-copy.db"
    shutil.copy(LIVE_DB, copy)
    db = InvestmentDB(copy)

    series = await MS.load_series(db)
    start, end = MS.PINNED_WINDOW
    stack = await MS.run_market_signal(db, start=start, end=end, series=series)
    control = await MS.run_trend_baseline(db, start=start, end=end, series=series)
    stack_m = await MS.stack_metrics(db, stack, until=end)
    control_m = await MS.stack_metrics(db, control, until=end)

    assert stack_m.sortino is not None and control_m.sortino is not None
    assert stack_m.max_drawdown is not None and control_m.max_drawdown is not None

    gap_pp = abs(stack_m.max_drawdown - control_m.max_drawdown) * 100.0
    assert gap_pp <= MS.DRIFT_TOLERANCE_PP, (
        "the stack and its frozen-book control no longer share a max drawdown "
        f"(stack {stack_m.max_drawdown * 100:.2f}%, control {control_m.max_drawdown * 100:.2f}%, "
        f"{gap_pp:.2f}pp apart) — book selection has started moving the number the "
        "-25% cap binds, which contradicts the attribution and rule_revision.adopt"
    )
    assert stack_m.sortino > control_m.sortino, (
        f"the signal layer no longer pays: stack Sortino {stack_m.sortino:.3f} vs frozen-book "
        f"control {control_m.sortino:.3f} (CAGR {(stack_m.cagr or 0) * 100:.2f}% vs "
        f"{(control_m.cagr or 0) * 100:.2f}%). The overlay carries the strategy; the "
        "credit/slope read is what ADR-007 claims to add, and it is no longer adding it."
    )


@pytest.mark.skipif(not LIVE_DB.exists(), reason=_SKIP_REASON)
async def test_the_walk_decides_on_the_previous_close_not_the_decision_day(tmp_path: Path) -> None:
    """NO LOOK-AHEAD (owner decision, 2026-08-13; `StackSeries.decision_prices`).

    `shadow_book_nav` applies a target dated t before day t's return, so a trend
    read taken from day t's CLOSE sets weights that then earn that same day —
    information no implementer has when the order goes in. It is worth ~1pp/yr
    on this rule, because a momentum rule reading today's close is systematically
    positioned for today's move: measured, removing it took the pinned CAGR from
    11.82% to 11.27% with the drawdown untouched.

    THIS IS THE TEST THAT WAS MISSING. Wiring the lag changed the live rule and
    the whole suite stayed green, which means nothing held the separation and
    nothing would have noticed it being undone. Two assertions, both structural:
    the decision view is exactly one trading day behind the pricing view, and
    what the DECISION actually journalled is the earlier number.

    A price series' own previous row, deliberately, not the shared calendar's:
    an instrument's previous close is its own, whether or not the defender's NAV
    index happens to carry that day."""
    copy = tmp_path / "live-copy.db"
    shutil.copy(LIVE_DB, copy)
    db = InvestmentDB(copy)

    series = await MS.load_series(db)
    for ticker in MS.TREND_SLEEVES:
        priced, decided = series.prices[ticker], series.decision_prices[ticker]
        assert decided.index.equals(priced.index)
        # value at row i of the decision view == value at row i-1 of the priced one
        assert decided.iloc[1:].to_numpy().tolist() == priced.iloc[:-1].to_numpy().tolist()

    run = await MS.run_market_signal(db, start=MS.PINNED_WINDOW[0], end=MS.PINNED_WINDOW[1])
    decision = run.decisions[-1]
    read = decision.trend["SPY"]
    same_day = float(series.prices["SPY"].loc[decision.date])
    previous = float(series.decision_prices["SPY"].loc[decision.date])
    assert read.price == previous, "the journalled read is not the previous close"
    assert read.price != same_day, "the walk read the decision day's own close — look-ahead is back"


@pytest.mark.skipif(not LIVE_DB.exists(), reason=_SKIP_REASON)
async def test_latest_trend_reads_matches_the_calendars_own_tail(tmp_path: Path) -> None:
    """`latest_trend_reads` (2026-08-19, the Stack page's "Latest" column)
    re-runs `_trend_read` at `series.calendar[-1]` instead of a decision date —
    this is the same reproduction guarantee the rest of this file enforces for
    the decision path, applied to the live snapshot: its numbers must be
    exactly what a direct read of the series' own tail would give, not an
    approximation of it.

    THE MOVING AVERAGES ARE RECOMPUTED UNSHIFTED (2026-08-19 fix: "LATEST n'a
    pas son overlay mis a jour" — the first version paired today's own close
    with `series.moving_averages`, which is lagged one day for the decision
    path's look-ahead guard, so the two numbers on a live row were never
    quoted on the same day). The expected value here is therefore a FRESH
    rolling mean off `series.prices` directly, not `series.moving_averages`."""
    copy = tmp_path / "live-copy.db"
    shutil.copy(LIVE_DB, copy)
    db = InvestmentDB(copy)

    series = await MS.load_series(db)
    t, reads = MS.latest_trend_reads(series)

    assert t == series.calendar[-1]
    assert set(reads) == {*MS.TREND_SLEEVES, MS.TREND_HAVEN}
    for ticker, read in reads.items():
        assert read.price == float(series.prices[ticker].loc[t])
        for window, ma in zip(series.moving_averages, read.moving_averages, strict=True):
            fresh = series.prices[ticker].rolling(window, min_periods=window).mean()
            expected = float(fresh.loc[t])
            if ma is None:
                assert pd.isna(expected)
            else:
                assert ma == expected
        # `below`/`share` must be derivable from the two numbers above alone —
        # no hidden state, same discipline `TrendRead.breached`'s docstring
        # describes for the decision path.
        ready = [ma for ma in read.moving_averages if ma is not None]
        breached = sum(1 for ma in ready if read.price < ma)
        assert read.share == (breached / len(ready) if ready else 0.0)
        assert read.below == (read.share > 0.0)


@pytest.mark.skipif(not LIVE_DB.exists(), reason=_SKIP_REASON)
async def test_latest_signal_reads_matches_the_calendars_own_tail(tmp_path: Path) -> None:
    """`latest_signal_reads`'s sibling guarantee: CREDIT_SPREAD/YIELD_SLOPE at
    the tail must match the same ffilled, calendar-aligned series `walk_decisions`
    reads at a decision date — just taken at `series.calendar[-1]` instead."""
    copy = tmp_path / "live-copy.db"
    shutil.copy(LIVE_DB, copy)
    db = InvestmentDB(copy)

    series = await MS.load_series(db)
    t = series.calendar[-1]
    signals = MS.latest_signal_reads(series)

    assert set(signals) == {MS.CREDIT_SPREAD, MS.YIELD_SLOPE}
    assert signals[MS.CREDIT_SPREAD]["value"] == float(series.spread.loc[t])
    assert signals[MS.CREDIT_SPREAD]["trailing_median"] == float(series.spread_median.loc[t])
    assert signals[MS.YIELD_SLOPE]["value"] == float(series.slope.loc[t])
    assert signals[MS.YIELD_SLOPE]["trailing_median"] == float(series.slope_median.loc[t])
