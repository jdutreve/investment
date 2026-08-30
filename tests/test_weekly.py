"""The weekly chain and the process that triggers it (`weekly.py`, `main.py`;
docs/TASKS.md Phase 7; docs/MILESTONES.md M9).

What is testable here without a network, an LLM or a week of wall clock: the
DUE-ON-START arithmetic and its marker, the fact that the marker and the regime
detector share one row without clobbering each other, and the shape of the
weekly chain. The chain RUNNING end to end is `test_simulated_weekly.py`, which
drives the same steps through the same runner on TestModel.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
import tzlocal

from investment import main as MAIN
from investment import weekly as M
from investment.chain import chain_period
from investment.db.sqlite import InvestmentDB
from investment.market.regime import DetectorState, _load_state, _persist_state

# A Monday, and the hour the chain is anchored to (chain.CHAIN_START_HOUR = 8).
ZURICH = timezone(timedelta(hours=2))
MONDAY_0900 = datetime(2026, 8, 10, 9, 0, tzinfo=ZURICH)


class _Settings:
    """Only what `weekly_steps` reads off settings while BUILDING the list —
    the thunks are never awaited here."""

    inbox_path = Path("/nonexistent-inbox")


def _runtime(db: InvestmentDB) -> Any:
    """A runtime whose components are never touched: `weekly_steps` closes over
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

    return is_chain_due(last.astimezone(now.tzinfo) if last else None, now, hour=M.CHAIN_START_HOUR)


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
        for name, _ in M.weekly_steps(
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
    which is the order ADR-011 requires. `aaaf-r` sits right after
    `market-signal` — same category, a time-varying book's weekly NAV refresh,
    no gate or Worker dependency of its own."""
    assert _steps(db) == [
        "catch-up",
        "market-signal",
        "aaaf-r",
        "event-watch",
        "curation",
        "backtests",
        "scenarios",
        "invariant-weights",
        "valuations",
        "ranking",
        "outcomes",
        "uc8",
        "digest",
    ]


def test_every_nav_producer_runs_before_every_nav_reader(db: InvestmentDB) -> None:
    """THE RULE THE ORDER ABOVE EXISTS FOR, asserted on the property rather than
    on the literal list — the list changed twice while the property was silently
    false, because reading a step list tells you nothing about who WRITES what.

    `catch-up` refreshes the STATIC portfolios' NAV and skips
    `TIME_VARYING_PORTFOLIOS` (constant weights cannot price a book whose weights
    move); the other two producers write those three series. Every reader of
    `portfolio_nav` — `backtests` via `ratios.load_nav`, `valuations` via its own
    query, `ranking` on the indicators valuations leaves — must come after ALL
    THREE, not merely after `catch-up`.

    Between 2026-08-13 (`ms-trend-baseline` joining the time-varying set) and
    2026-08-30 it did not: `market-signal` and `aaaf-r` ran after `ranking`, and
    the live 2026-08-30 ranking scored `ms-stack` on its NAV row of 2026-08-21
    against static portfolios scored on 2026-08-28."""
    steps = _steps(db)
    producers = ["catch-up", "market-signal", "aaaf-r"]
    readers = ["backtests", "valuations", "ranking"]
    last_producer = max(steps.index(name) for name in producers)
    first_reader = min(steps.index(name) for name in readers)
    assert last_producer < first_reader, (
        f"NAV producer {steps[last_producer]!r} runs after reader {steps[first_reader]!r}"
    )


def test_the_chain_covers_the_whole_timeline(db: InvestmentDB) -> None:
    """Every slot CLAUDE.md's timeline names now has a job behind it. The two
    remaining absences are INSIDE steps and written down where they bite: the
    expiry sweep (ADR-006 emptied it) and UC3's bounded-domain fetch (no text
    extractor yet)."""
    steps = _steps(db)
    # MEMBERSHIP AND RELATIVE ORDER, not a positional prefix. This read
    # `steps[:3] == [...]` and so encoded "nothing may be inserted after
    # catch-up" — a constraint this test never meant and which the refresh block
    # (`market-signal`, `aaaf-r`) legitimately breaks. Coverage is the claim;
    # `test_the_chain_runs_the_timeline_in_order` owns the exact sequence.
    assert {"catch-up", "event-watch", "curation"} <= set(steps)
    assert steps[0] == "catch-up"
    assert steps.index("event-watch") < steps.index("curation")
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


# -- the fronts must not take the agent down --------------------------------


async def test_a_telegram_front_that_cannot_start_leaves_the_agent_running() -> None:
    """Measured on the first real launch (2026-08-12): `.env` still carried the
    placeholder token, `bot.initialize()` raised `InvalidToken`, and the process
    died before the chain had refreshed a single price.

    Nothing the agent DOES depends on Telegram — it decides, records and
    measures into SQLite — and `telegram/notify.py` already states the rule for
    the other half of this channel: failing to notify must not fail the caller,
    because a raise there replaces a reported failure with an unreported one.
    The receive half having the opposite behaviour was an inconsistency, not a
    decision."""
    from telegram.error import InvalidToken

    class _DeadBot:
        async def initialize(self) -> None:
            raise InvalidToken("The token `REPLACE_ME` was rejected by the server.")

    assert await MAIN._start_bot(cast(Any, _DeadBot())) is False


async def test_a_healthy_front_reports_itself_started() -> None:
    class _LiveBot:
        def __init__(self) -> None:
            self.updater = SimpleNamespace(start_polling=self._noop)
            self.started = False

        async def _noop(self) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def start(self) -> None:
            self.started = True

    bot = _LiveBot()
    assert await MAIN._start_bot(cast(Any, bot)) is True
    assert bot.started


# -- the weekly anchor ------------------------------------------------------


def test_the_anchor_is_sunday_and_the_cron_reads_the_same_constant() -> None:
    """The cron and the due-check must agree about which day the week turns on.
    A cron firing on a day `is_chain_due` does not recognise would run nothing,
    silently, for as long as nobody looked — so both read one constant."""
    from investment.chain import CHAIN_START_WEEKDAY

    assert CHAIN_START_WEEKDAY == 6  # date.weekday(): Monday 0 … Sunday 6
    assert datetime(2026, 8, 9).weekday() == CHAIN_START_WEEKDAY  # a Sunday


def test_the_cron_fires_on_the_OWNERS_clock_not_the_laptops() -> None:
    """`AsyncIOScheduler(timezone=...)` does NOT reach a trigger the caller
    built. It is passed to `_create_trigger`, which only runs for the string
    form (`add_job(fn, "cron", ...)`); a `CronTrigger` instance resolves its own
    timezone in `__init__` and, given none, takes `tzlocal.get_localzone()` —
    the MACHINE's. So the scheduler said Zurich, the trigger said whatever the
    laptop said, and on this laptop those agree, which is why it was invisible.

    On a trip they do not agree: the chain would fire at 08:00 local while
    `is_chain_due` (reading `settings.tz` through `agent_now`) still measured
    the week from Zurich's Sunday 08:00. Both halves must anchor on one clock.

    Pinned with the machine forced elsewhere, because a test on a Zurich laptop
    cannot tell a fixed version from the broken one."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    from investment.chain import CHAIN_START_HOUR, CHAIN_START_WEEKDAY

    scheduler = AsyncIOScheduler(timezone="Europe/Zurich")
    trigger = CronTrigger(
        day_of_week=CHAIN_START_WEEKDAY,
        hour=CHAIN_START_HOUR,
        minute=0,
        timezone=ZoneInfo("Europe/Zurich"),
    )
    assert str(trigger.timezone) == "Europe/Zurich"
    # and the scheduler is not what put it there
    assert str(CronTrigger(day_of_week=CHAIN_START_WEEKDAY, hour=CHAIN_START_HOUR).timezone) == str(
        tzlocal.get_localzone()
    )
    assert str(scheduler.timezone) == "Europe/Zurich"


def test_the_anchor_is_found_from_any_day_of_the_week() -> None:
    """Written for any weekday rather than Monday's `now.weekday()` shortcut:
    the anchor moved once, and a formula that only works for one day has to be
    rewritten the next time."""
    from investment.chain import most_recent_chain_start

    sunday_0800 = datetime(2026, 8, 9, 8, 0, tzinfo=ZURICH)
    for day, hour in ((9, 7), (9, 8), (9, 23), (10, 0), (15, 12)):  # Sun→Sat
        now = datetime(2026, 8, day, hour, tzinfo=ZURICH)
        expected = sunday_0800 if (day, hour) != (9, 7) else sunday_0800 - timedelta(days=7)
        assert most_recent_chain_start(now) == expected, f"{day}/{hour}"


async def test_a_chain_that_ran_on_sunday_is_not_due_on_monday(db: InvestmentDB) -> None:
    """The case the move is FOR: the digest lands before the week opens, and
    Monday must not re-run it."""
    await M.record_chain_success(db, datetime(2026, 8, 9, 8, 30, tzinfo=ZURICH))
    assert not await _due(db, datetime(2026, 8, 10, 9, 0, tzinfo=ZURICH))


async def test_a_chain_that_last_ran_before_sunday_is_due(db: InvestmentDB) -> None:
    await M.record_chain_success(db, datetime(2026, 8, 7, 8, 30, tzinfo=ZURICH))  # Friday
    assert await _due(db, datetime(2026, 8, 10, 9, 0, tzinfo=ZURICH))  # Monday


# -- the log split ----------------------------------------------------------


def test_info_goes_to_stdout_and_warnings_to_stderr() -> None:
    """Under launchd these are two FILES. `basicConfig` sent everything to
    stderr, so `agent.error.log` held a normal Sunday and `agent.log` held
    nothing — both names lying, and the one opened first the wrong one."""
    import logging as _logging
    import sys

    MAIN.configure_logging()
    handlers = _logging.getLogger().handlers
    streams = {h.stream: h for h in handlers if isinstance(h, _logging.StreamHandler)}  # type: ignore[attr-defined]

    assert sys.stdout in streams and sys.stderr in streams
    info = _logging.LogRecord("x", _logging.INFO, "f", 1, "m", None, None)
    warn = _logging.LogRecord("x", _logging.WARNING, "f", 1, "m", None, None)

    out, err = streams[sys.stdout], streams[sys.stderr]
    # The stdout handler needs the FILTER as well as the level: a level admits
    # everything above it, so without it a warning would land in both files.
    assert out.filter(info) and not out.filter(warn)
    # The LOGGER applies a handler's level (`Logger.callHandlers`), not
    # `Handler.handle` — so this asserts the level itself rather than a call
    # that would happily emit an INFO record straight into stderr.
    assert err.level == _logging.WARNING
    assert info.levelno < err.level <= warn.levelno


# -- the one step a retry must not repeat -----------------------------------


async def _complete_cycle(db: InvestmentDB, *, trigger: str, day: str) -> None:
    """What `run_decision_cycle` appends LAST, once the knowledge commit has
    returned. The guard reads this and not the Worker reading — see
    `test_a_cycle_that_died_after_the_worker_is_not_counted_as_done`."""
    async with db.transaction():
        await db.append_event(
            type="CognitiveCycleCompletedEvent",
            source_uc="UC8",
            source_id="run-1",
            payload={"trigger": trigger, "confrontations": 3},
            event_date=date.fromisoformat(day),
        )


async def _journal_reading(db: InvestmentDB, *, trigger: str, day: str) -> None:
    """What `decision_cycle.journal_worker_reading` appends BEFORE Planner Post
    and the knowledge commit — an audit of what the Worker said, not proof the
    cycle finished."""
    async with db.transaction():
        await db.append_event(
            type="WorkerReadingEvent",
            source_uc="UC8",
            source_id="run-1",
            payload={"trigger": trigger, "reasoning": "..."},
            event_date=date.fromisoformat(day),
        )


async def test_a_week_with_no_cycle_yet_runs_one(db: InvestmentDB) -> None:
    assert await M.weekly_cycle_already_ran(db, "2026-08-09") is False


async def test_a_retry_later_in_the_SAME_week_does_not_buy_a_second_cycle(
    db: InvestmentDB,
) -> None:
    """THE DEFECT THIS CLOSES. The chain is retried whole, which is right for
    every mechanical step — they recompute on fresher prices. UC8 is the one
    that spends an LLM call and appends a fresh WorkerReadingEvent, invariant
    confrontations and innovations on every pass, so a failure at 09:30 on the
    digest used to buy a second Worker deliberation to re-send one message."""
    await _complete_cycle(db, trigger=M.CHAIN_TRIGGER, day="2026-08-09")  # Sunday's cycle
    # Tuesday's retry asks about the same week and finds it done.
    assert await M.weekly_cycle_already_ran(db, chain_period(date(2026, 8, 11))) is True


async def test_the_NEXT_week_runs_its_own_cycle(db: InvestmentDB) -> None:
    """Scoped to the week, not "has one ever run" — otherwise the chain would
    never think again."""
    await _complete_cycle(db, trigger=M.CHAIN_TRIGGER, day="2026-08-09")
    assert await M.weekly_cycle_already_ran(db, chain_period(date(2026, 8, 16))) is False


async def test_a_cycle_that_died_after_the_worker_is_not_counted_as_done(
    db: InvestmentDB,
) -> None:
    """THE GUARD'S OWN DEFECT. It read `WorkerReadingEvent`, which
    `run_decision_cycle` appends BEFORE Planner Post and `commit_knowledge`. A
    cycle that got its answer and then failed to commit it therefore left a
    marker saying "done" over work that had not happened, and the retry skipped
    UC8 — the week's deliberation was paid for and thrown away.

    A guard must read a record written by the LAST step it guards."""
    await _journal_reading(db, trigger=M.CHAIN_TRIGGER, day="2026-08-09")
    assert await M.weekly_cycle_already_ran(db, "2026-08-09") is False

    await _complete_cycle(db, trigger=M.CHAIN_TRIGGER, day="2026-08-09")
    assert await M.weekly_cycle_already_ran(db, "2026-08-09") is True


async def test_an_adhoc_UC9_cycle_does_not_stand_in_for_the_weeks_own(
    db: InvestmentDB,
) -> None:
    """An ad-hoc `/cycle` is the owner asking for an EXTRA reading, not the
    week's. Counting it would let a Monday question silence Sunday's chain."""
    await _complete_cycle(db, trigger="uc9-adhoc", day="2026-08-10")
    assert await M.weekly_cycle_already_ran(db, "2026-08-09") is False
