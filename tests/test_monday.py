"""The Monday chain and the process that triggers it (`monday.py`, `main.py`;
docs/TASKS.md Phase 7; docs/MILESTONES.md M9).

What is testable here without a network, an LLM or a week of wall clock: the
DUE-ON-START arithmetic and its marker, the fact that the marker and the regime
detector share one row without clobbering each other, and the shape of the
Monday chain. The chain RUNNING end to end is `test_simulated_monday.py`, which
drives the same steps through the same runner on TestModel.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from investment import main as MAIN
from investment import monday as M
from investment.db.sqlite import InvestmentDB
from investment.market.regime import DetectorState, _load_state, _persist_state

# A Monday, and the hour the chain is anchored to (chain.CHAIN_START_HOUR = 8).
ZURICH = timezone(timedelta(hours=2))
MONDAY_0900 = datetime(2026, 8, 10, 9, 0, tzinfo=ZURICH)


class _Settings:
    """Only what `monday_steps` reads off settings while BUILDING the list —
    the thunks are never awaited here."""

    inbox_path = Path("/nonexistent-inbox")


def _runtime(db: InvestmentDB) -> Any:
    """A runtime whose components are never touched: `monday_steps` closes over
    them and the thunks are not awaited, so building two OpenRouter agents and
    loading a 90MB embedder to look at a list of names would buy nothing."""
    return SimpleNamespace(db=db, settings=_Settings())


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "main.db")
    yield conn
    await conn.close()


# -- the DUE-ON-START marker ------------------------------------------------


async def test_no_marker_means_the_chain_has_never_run(db: InvestmentDB) -> None:
    assert await M.last_chain_success(db) is None


async def test_the_marker_round_trips(db: InvestmentDB) -> None:
    when = datetime(2026, 8, 10, 6, 30, tzinfo=UTC)
    await M.record_chain_success(db, when)
    assert await M.last_chain_success(db) == when


async def test_the_marker_does_not_clobber_the_detector_state(db: InvestmentDB) -> None:
    """`detector_state` is ONE row with TWO writers (docs/DATA_MODELS.md). A
    chain that reset the hysteresis streak would silently re-arm a regime
    change — the streak is what `regime_confirm_prints` counts."""
    await _persist_state(
        db,
        DetectorState(
            candidate_type="stagflation",
            candidate_start_ts="2026-07-01",
            consecutive_prints=2,
            last_print_ts_growth="2026-08-01",
            last_print_ts_inflation="2026-08-01",
        ),
    )
    await M.record_chain_success(db, datetime(2026, 8, 10, 6, 30, tzinfo=UTC))

    state = await _load_state(db)
    assert state.candidate_type == "stagflation"
    assert state.consecutive_prints == 2
    assert state.last_print_ts_growth == "2026-08-01"


async def test_the_detector_does_not_clobber_the_marker(db: InvestmentDB) -> None:
    """The same invariant from the other side, pinned here because it is a
    property of `regime._persist_state`'s column list and nothing else states
    it: a detector step that cleared this marker would make the chain look due
    again and re-run the whole week on the next heartbeat."""
    when = datetime(2026, 8, 10, 6, 30, tzinfo=UTC)
    await M.record_chain_success(db, when)
    await _persist_state(db, DetectorState(None, None, 1, "2026-08-03", "2026-08-03"))
    assert await M.last_chain_success(db) == when


# -- due-on-start -----------------------------------------------------------


async def _due(db: InvestmentDB, now: datetime) -> bool:
    """Whether `chain_if_due` would run, without running it: it delegates the
    arithmetic to `chain.is_chain_due` and the only thing this module adds is
    reading the marker and converting it to local time."""
    last = await M.last_chain_success(db)
    from investment.chain import is_chain_due

    return is_chain_due(last.astimezone(now.tzinfo) if last else None, now, M.CHAIN_START_HOUR)


async def test_a_chain_that_ran_this_morning_is_not_due_again(db: InvestmentDB) -> None:
    await M.record_chain_success(db, MONDAY_0900 - timedelta(minutes=30))
    assert not await _due(db, MONDAY_0900)


async def test_a_chain_that_last_ran_before_monday_is_due(db: InvestmentDB) -> None:
    """The lid-closed case (ADR-002): the Mac slept through Monday 08:00, the
    cron never fired, and the first heartbeat after wake must catch it."""
    await M.record_chain_success(db, MONDAY_0900 - timedelta(days=3))
    assert await _due(db, MONDAY_0900)


async def test_the_marker_is_stored_in_utc_and_compared_in_local_time(db: InvestmentDB) -> None:
    """The naive-vs-aware trap this pair of conversions exists to close. The
    marker is UTC; "the most recent Monday 08:00" is a statement about the
    owner's calendar. Stored 06:30Z IS 08:30 in Zurich — after the anchor — so a
    comparison that forgot the conversion would call this chain due and run it
    twice."""
    await M.record_chain_success(db, datetime(2026, 8, 10, 6, 30, tzinfo=UTC))
    assert not await _due(db, MONDAY_0900)


# -- the chain's shape ------------------------------------------------------


def _steps(db: InvestmentDB) -> list[str]:
    """Step NAMES only. The thunks are never awaited here, so the Planner and
    Worker objects they close over are never used — `cast` keeps the test
    hermetic rather than building two OpenRouter agents to look at a list."""

    async def send(_: str) -> bool:
        return True

    return [
        name
        for name, _ in M.monday_steps(
            _runtime(db),
            thresholds={"rolling_window_days": 756.0, "ranking_tiebreak_window": 0.02},
            user_profile={"max_single_asset_pct": 50.0, "max_drawdown_pct": -25.0},
            run_id="run-1",
            today=MONDAY_0900.date(),
            send=send,
        )
    ]


def test_the_chain_runs_the_timeline_in_order(db: InvestmentDB) -> None:
    """CLAUDE.md pins the order and each step feeds the next: UC6 refills the
    indicators UC7 ranks on, so ranking before valuing would rank on stale
    numbers; the market-signal decision is journalled BEFORE the Worker speaks,
    which is the order ADR-011 requires."""
    assert _steps(db) == [
        "catch-up",
        "event-watch",
        "curation",
        "backtests",
        "scenarios",
        "invariant-weights",
        "valuations",
        "ranking",
        "outcomes",
        "market-signal",
        "uc8",
        "digest",
    ]


def test_the_chain_covers_the_whole_timeline(db: InvestmentDB) -> None:
    """Every slot CLAUDE.md's timeline names now has a job behind it. The two
    remaining absences are INSIDE steps and written down where they bite: the
    expiry sweep (ADR-006 emptied it) and UC3's bounded-domain fetch (no text
    extractor yet)."""
    steps = _steps(db)
    assert steps[:3] == ["catch-up", "event-watch", "curation"]
    assert "market-signal" in steps and "uc8" in steps


def test_the_digest_is_last_so_nothing_renders_on_half_computed_state(db: InvestmentDB) -> None:
    steps = _steps(db)
    assert steps[-1] == "digest"
    assert steps.index("market-signal") < steps.index("uc8") < steps.index("digest")


# -- the heartbeat ----------------------------------------------------------


async def test_the_heartbeat_stops_immediately_on_the_stop_event() -> None:
    """Waiting on the event with a timeout rather than sleeping: shutdown is
    immediate instead of up to five minutes late."""
    import asyncio

    stop = asyncio.Event()
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        ticks += 1

    stop.set()
    await asyncio.wait_for(MAIN.heartbeat(stop, tick), timeout=1.0)
    assert ticks == 0


async def test_a_failing_tick_does_not_kill_the_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is the WAKE path. A process that stops asking whether the chain is due
    looks exactly like a week with nothing to do — the silent failure this whole
    milestone is built against."""
    import asyncio

    monkeypatch.setattr(MAIN, "HEARTBEAT_SECONDS", 0.01)
    stop = asyncio.Event()
    calls = 0

    async def tick() -> None:
        nonlocal calls
        calls += 1
        if calls >= 3:
            stop.set()
        raise RuntimeError("boom")

    await asyncio.wait_for(MAIN.heartbeat(stop, tick), timeout=2.0)
    assert calls >= 3
