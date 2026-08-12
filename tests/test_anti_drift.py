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
