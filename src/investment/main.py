"""The agent PROCESS — what runs on the Mac and makes everything else happen
(docs/TASKS.md Phase 7; docs/MILESTONES.md M9).

Everything below this file is a library: jobs that do one thing when called, a
chain runner that sequences thunks, a digest that renders. Nothing of it runs by
itself. This is the one module whose job is TIME — deciding when the week's work
is due, holding the lock that stops two of them overlapping, and telling the
owner when it broke.

FOUR THINGS RUN HERE, in one asyncio process (ADR-002/ADR-004: one machine, one
connection, one writer):

  1. the INBOX WATCHER  — 60s poll, 5-minute quiet period, then an ingestion
     batch (corpus/watcher.py). Event-driven, not scheduled.
  2. the MONDAY CRON    — 08:00 Europe/Zurich, the normal path.
  3. the HEARTBEAT      — every 5 minutes, asks whether the chain is DUE. This
     is the wake path (ADR-002: the laptop sleeps, so a cron that fires at 08:00
     on a closed lid never fires at all), and also the launch path.
  4. SHUTDOWN           — SIGTERM/SIGINT finish the current transaction,
     checkpoint the WAL and close (CLAUDE.md "Dev standards").

WHY THE CRON AND THE HEARTBEAT ARE BOTH HERE, given they can both fire on a
Monday morning: they answer different questions. The cron is punctual and the
heartbeat is a catch-up, and neither can know whether the other ran. What makes
the redundancy safe is that BOTH go through `chain_if_due`, which is guarded
twice over — by `detector_state.last_chain_success` (a chain that already
succeeded this week is not due) and by the run-lock (a chain already running
refuses the second caller). Redundant triggers with an idempotent target is the
shape ADR-002 forces on a laptop that sleeps.

THE GO-LIVE GATE IS NOT HERE (ADR-013). Task 9.3 gave this file a startup gate
that skipped UC8 unless a mechanical replay report showed the agent's proposals
beat holding the defender. ADR-012 removed the object of that predicate — UC8
allocates nothing — and the path that does move money runs at 08:55 and was
never covered by it. Forward paper-mode is the go-live gate (ADR-007 §5); the
binding caps, ADR-009's alerts and the anti-drift check are what watch the
allocator.

THE CHAIN IS COMPLETE. Every step CLAUDE.md's timeline names now has a job:
08:00 catch-up (`mechanical/catchup.py`), 08:05 UC3 event watch
(`watch/event_watch.py`), 08:10 UC4 curation sweep (`corpus/curation_sweep.py`),
then the mechanical block, the monthly allocation decision, UC8 and the digest.

Two absences are deliberate and written down where they bite: the expiry sweep
CLAUDE.md lists beside the catch-up stays unwired (ADR-006 removed the user
response 'expired' described — `catchup.py`), and UC3's bounded-domain fetch
waits for a text extractor this project does not have (`event_watch.py`).
"""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic_ai import Agent
from ulid import ULID

from investment.chain import CHAIN_START_HOUR, ChainResult, ChainStep, is_chain_due, run_chain
from investment.config import Settings
from investment.corpus.curation_sweep import sweep_corpus
from investment.corpus.embedding import InProcessEmbedder
from investment.corpus.ingester import CorpusIngester
from investment.corpus.watcher import InboxWatcher
from investment.db.backup import backup_database
from investment.db.sqlite import InvestmentDB
from investment.decision_cycle import run_decision_cycle
from investment.market_signal_cycle import run_market_signal_cycle
from investment.mechanical.as_of_cycle import reweigh_invariants_asof
from investment.mechanical.backtests import run_backtests_and_favors
from investment.mechanical.catchup import run_catchup
from investment.mechanical.outcomes import evaluate_proposals, strategy_probation_check
from investment.mechanical.ratios import value_portfolios
from investment.mechanical.scenarios import warm_start_scenario_probabilities
from investment.mechanical.snapshots import build_snapshot
from investment.ops.run_lock import AlreadyRunning, RunLock
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
from investment.telegram.digest import build_digest
from investment.telegram.notify import notify
from investment.watch.event_watch import (
    EventTriage,
    build_triage_agent,
    flagged_message,
    run_event_watch,
)
from investment.worker.agent import build_worker_agent
from investment.worker.result import WorkerResult

logger = logging.getLogger(__name__)

# How often the wake/launch path asks whether the chain is due. Five minutes is
# the spec's own figure (Phase 7) and the reasoning is asymmetric: a late chain
# costs the owner a few minutes of a weekly report, while a tight loop costs a
# wakeup on a laptop that is asleep most of the time. It is also short enough
# that "I opened the lid on Tuesday" produces the Monday chain before coffee.
HEARTBEAT_SECONDS = 300.0

# The chain's own name in the run-lock. The other holders arrive with the
# command layer ({catchup, uc8, replay} — Task 6ter.1).
CHAIN_LOCK = "monday-chain"


async def last_chain_success(db: InvestmentDB) -> datetime | None:
    """When the Monday chain last completed, or None if it never has.

    `detector_state.last_chain_success` (docs/DATA_MODELS.md: "ISO-8601, drives
    DUE-ON-START, a timestamp, NOT a FLOAT config threshold, so it lives here
    and not in system_thresholds"). A marker rather than a scan of the EventLog:
    the chain appends an ErrorEvent when it FAILS and nothing at all when it
    succeeds, so "the last success" is not derivable from the log."""
    rows = await db.query("SELECT last_chain_success FROM detector_state WHERE id = 'singleton'")
    if not rows or not rows[0]["last_chain_success"]:
        return None
    return datetime.fromisoformat(str(rows[0]["last_chain_success"]))


async def record_chain_success(db: InvestmentDB, when: datetime) -> None:
    """Stamp the marker `is_chain_due` reads.

    The UPSERT touches `last_chain_success` AND NOTHING ELSE, exactly as
    `regime._persist_state` touches the detector's columns and not this one.
    The two writers share one row and must not clobber each other: a chain that
    reset the hysteresis streak would silently re-arm a regime change, and a
    detector step that cleared this marker would re-run the whole chain on the
    next heartbeat."""
    async with db.transaction():
        await db.command(
            "INSERT INTO detector_state (id, consecutive_prints, last_chain_success, updated_at) "
            "VALUES ('singleton', 0, :when, :now) "
            "ON CONFLICT(id) DO UPDATE SET "
            " last_chain_success = excluded.last_chain_success, "
            " updated_at = excluded.updated_at",
            when=when.isoformat(),
            now=datetime.now(UTC).isoformat(),
        )


def monday_steps(
    db: InvestmentDB,
    *,
    thresholds: dict[str, float],
    user_profile: dict[str, Any],
    planner_pre: PlannerPre,
    worker_agent: Agent[None, WorkerResult],
    planner_post: PlannerPost,
    run_id: str,
    today: date,
    send: Callable[[str], Awaitable[bool]],
    settings: Settings,
    embedder: InProcessEmbedder,
    ingester: CorpusIngester,
    triage_agent: Agent[None, EventTriage],
) -> list[ChainStep]:
    """The Monday chain, in the timeline CLAUDE.md pins (08:30 → 09:30).

    THE SAME FUNCTIONS `as_of_cycle.run_as_of_cycle` CALLS, in the same order,
    and that is not a coincidence to preserve by hand: the agentic replay
    hydrates its snapshots by running the live jobs, precisely so replay logic
    cannot drift from live logic (Task 9.4). If a step is added here it belongs
    there too, and the reverse.

    Each step is a THUNK so `run_chain` can sequence jobs with different
    signatures without knowing any of them (chain.py). The digest is the last
    step rather than a separate concern: a chain that aborts sends the owner an
    ErrorEvent alert instead, which is the correct substitution — a digest
    rendered on half-computed state would be worse than none."""
    window = int(thresholds["rolling_window_days"])

    async def event_watch() -> None:
        """08:05. The flagged items are pushed to Telegram HERE rather than
        waiting for the digest: `needs-user-input` is a question to the owner,
        and a question that arrives four hours later inside a weekly report is
        not a question (UC3: "flagged and pushed to Telegram instead of being
        hallucinated")."""
        report = await run_event_watch(db, ingester, triage_agent)
        if report.flagged:
            await send(flagged_message(report.flagged))

    async def outcomes() -> None:
        """The 08:52 slot is TWO jobs (CLAUDE.md: "verdicts +12w, calibration,
        probation"). Scenario calibration is not among them: ADR-007 superseded
        it and `score_scenarios` was removed, which `digest.build_scoreboard`
        records by keeping `calibration_flags` empty."""
        await evaluate_proposals(db, today)
        await strategy_probation_check(db, today)

    async def cognitive_cycle() -> None:
        await run_decision_cycle(
            db,
            planner_pre,
            worker_agent,
            planner_post,
            trigger="monday-chain",
            user_profile=user_profile,
            thresholds=thresholds,
            today=today,
            run_id=run_id,
        )

    async def digest() -> None:
        """Render and SEND. Rendering without sending would leave the week's
        report in a log nobody reads, and the send is what the whole chain is
        for — the owner places the month's orders from it."""
        await send(await build_digest(db, today))

    return [
        # FIRST, and everything after it reads what it leaves: a chain that
        # ranked before refreshing would rank last week's world (UC1).
        ("catch-up", lambda: run_catchup(db, settings)),
        ("event-watch", event_watch),
        # 08:10. Free on a stable corpus — the checkpoint answers "already
        # curated" without an LLM call — and the retry path for an ingestion
        # whose curation failed during the week.
        ("curation", lambda: sweep_corpus(db, settings, embedder=embedder)),
        ("backtests", lambda: run_backtests_and_favors(db, window, today=today)),
        ("scenarios", lambda: warm_start_scenario_probabilities(db, today=today)),
        ("invariant-weights", lambda: reweigh_invariants_asof(db, today, thresholds)),
        ("valuations", lambda: value_portfolios(db, window)),
        ("ranking", lambda: build_snapshot(db, thresholds["ranking_tiebreak_window"], today)),
        ("outcomes", outcomes),
        ("market-signal", lambda: run_market_signal_cycle(db, user_profile, today=today)),
        ("uc8", cognitive_cycle),
        ("digest", digest),
    ]


async def run_monday_chain(
    db: InvestmentDB,
    settings: Settings,
    *,
    planner_pre: PlannerPre,
    worker_agent: Agent[None, WorkerResult],
    planner_post: PlannerPost,
    lock: RunLock,
    embedder: InProcessEmbedder,
    ingester: CorpusIngester,
    triage_agent: Agent[None, EventTriage],
    today: date | None = None,
) -> ChainResult | None:
    """One full Monday chain, under the run-lock. `None` when the lock refused.

    On SUCCESS: stamp `last_chain_success`, then back up. On FAILURE: alert the
    owner with the step that broke — `run_chain` already appended the ErrorEvent
    and never re-raises, so this function's job is to say it out loud.

    THE MARKER IS STAMPED ONLY ON A COMPLETE CHAIN. A partial run leaves the
    chain DUE, so the next heartbeat retries it — which is right: the steps that
    completed are idempotent (UPSERTs and check-before-append, CLAUDE.md), and
    the alternative is a week with no ranking because one fetch failed at 08:31.
    """
    today = today or date.today()
    run_id = str(ULID())
    token, chat = settings.telegram_bot_token, settings.telegram_chat_id

    async def send(text: str) -> bool:
        return await notify(token, chat, text)

    try:
        async with lock.hold(CHAIN_LOCK):
            thresholds = {
                str(r["key"]): float(r["value"])
                for r in await db.query("SELECT key, value FROM system_thresholds")
            }
            profile_rows = await db.query("SELECT * FROM user_profile LIMIT 1")
            if not profile_rows:
                raise RuntimeError("no user_profile row — run the seed first")

            steps = monday_steps(
                db,
                thresholds=thresholds,
                user_profile=dict(profile_rows[0]),
                planner_pre=planner_pre,
                worker_agent=worker_agent,
                planner_post=planner_post,
                run_id=run_id,
                today=today,
                send=send,
                settings=settings,
                embedder=embedder,
                ingester=ingester,
                triage_agent=triage_agent,
            )
            result = await run_chain(db, steps, run_id)
    except AlreadyRunning as exc:
        logger.info("monday chain skipped: %s", exc)
        return None

    if result.ok:
        await record_chain_success(db, datetime.now(UTC))
        # AFTER the marker, so a backup that fails cannot make the chain look
        # unfinished and re-run the whole week on the next heartbeat.
        await asyncio.to_thread(
            backup_database, settings.db_path, settings.db_path.parent / "backups", today=today
        )
        logger.info("monday chain %s complete: %s", run_id, result.completed)
    else:
        await send(
            f"🚨 Monday chain ABORTED at step '{result.failed_step}': {result.error}\n\n"
            f"Completed before it: {', '.join(result.completed) or 'nothing'}.\n"
            "The steps that ran stand; the chain stays DUE and will retry on the next "
            "heartbeat. Nothing was ranked or proposed on half-computed state."
        )
    return result


async def chain_if_due(
    db: InvestmentDB,
    settings: Settings,
    *,
    planner_pre: PlannerPre,
    worker_agent: Agent[None, WorkerResult],
    planner_post: PlannerPost,
    lock: RunLock,
    embedder: InProcessEmbedder,
    ingester: CorpusIngester,
    triage_agent: Agent[None, EventTriage],
    now: datetime | None = None,
) -> ChainResult | None:
    """DUE-ON-START: run the chain iff its last success predates the most recent
    Monday 08:00 (chain.py `is_chain_due`). `None` when it is not due.

    The naive-vs-aware comparison is settled HERE: the marker is written in UTC
    and `is_chain_due` compares against a local Monday 08:00, so `now` must be
    local wall-clock and the marker is converted to it. Europe/Zurich lives at
    the presentation edge (CLAUDE.md), and "the most recent Monday 08:00" is a
    statement about the owner's calendar, not about UTC."""
    now = now or datetime.now().astimezone()
    last = await last_chain_success(db)
    if not is_chain_due(last.astimezone(now.tzinfo) if last else None, now, CHAIN_START_HOUR):
        return None
    logger.info("monday chain is DUE (last success: %s)", last)
    return await run_monday_chain(
        db,
        settings,
        planner_pre=planner_pre,
        worker_agent=worker_agent,
        planner_post=planner_post,
        lock=lock,
        embedder=embedder,
        ingester=ingester,
        triage_agent=triage_agent,
        today=now.date(),
    )


async def heartbeat(stop: asyncio.Event, tick: Callable[[], Awaitable[Any]]) -> None:
    """Call `tick` every `HEARTBEAT_SECONDS` until `stop`.

    Waiting on the stop event with a timeout rather than sleeping, so shutdown
    is immediate instead of up to five minutes late — the same shape
    `InboxWatcher.run` uses, for the same reason.

    A tick that RAISES must not kill the heartbeat: it is the wake path, and a
    process that stops asking whether the chain is due looks exactly like a week
    with nothing to do. `run_monday_chain` already absorbs chain failures, so
    anything arriving here is unexpected — logged with its traceback, then the
    loop continues."""
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
            return
        try:
            await tick()
        except Exception:
            logger.exception("heartbeat tick failed; continuing")


async def run_agent(settings: Settings) -> None:
    """Wire everything and run until a signal arrives."""
    db = InvestmentDB(settings.db_path)
    if not await db.query("SELECT 1 FROM framework WHERE id = '4seasons'"):
        await db.close()
        raise RuntimeError("seed not run — execute `uv run python -m investment.seed` first")

    embedder = InProcessEmbedder(settings.embedding_model)
    planner_pre = PlannerPre(db, embedder, settings.planner_model, settings.openrouter_api_key)
    planner_post = PlannerPost(settings.planner_model, settings.openrouter_api_key)
    worker_agent = build_worker_agent(db, settings.worker_model, settings.openrouter_api_key)
    # The UC3 triage runs on the PLANNER model at the curator's effort: it is a
    # short structured judgement over one press item, the same shape of task the
    # curator does, and not the Worker's deliberation.
    triage_agent = build_triage_agent(
        settings.planner_model, settings.openrouter_api_key, settings.curator_reasoning_effort
    )
    # ONE ingester, shared by the inbox watcher and the event watch: both turn
    # text into Documents, and `from_db` reads the calibrated chunking once.
    ingester = await CorpusIngester.from_db(db, embedder)
    lock = RunLock()

    async def tick() -> None:
        await chain_if_due(
            db,
            settings,
            planner_pre=planner_pre,
            worker_agent=worker_agent,
            planner_post=planner_post,
            lock=lock,
            embedder=embedder,
            ingester=ingester,
            triage_agent=triage_agent,
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # `add_signal_handler` rather than `signal.signal`: it runs the callback
        # ON the event loop, so setting the event cannot race with the tasks
        # waiting on it. The handler does nothing but set — the shutdown itself
        # is ordinary async code below, where a transaction can finish.
        loop.add_signal_handler(sig, stop.set)

    watcher = InboxWatcher(db, ingester, settings.inbox_path, settings.sources_path)
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    # The punctual path. Its target is the same `tick` the heartbeat calls, so
    # whichever arrives first runs the chain and the other finds it not due.
    scheduler.add_job(tick, CronTrigger(day_of_week="mon", hour=CHAIN_START_HOUR, minute=0))

    tasks = [
        asyncio.create_task(watcher.run(stop), name="inbox-watcher"),
        asyncio.create_task(heartbeat(stop, tick), name="heartbeat"),
    ]
    scheduler.start()
    logger.info(
        "agent up: db=%s tz=%s inbox=%s (chain due-on-start check running)",
        settings.db_path,
        settings.tz,
        settings.inbox_path,
    )
    try:
        # DUE-ON-START, before waiting: a laptop opened on Tuesday must not wait
        # five minutes for the heartbeat to notice that Monday came and went.
        await tick()
        await stop.wait()
    finally:
        logger.info("shutting down")
        scheduler.shutdown(wait=False)
        stop.set()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await db.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # pydantic-settings populates required fields from .env at runtime; mypy
    # cannot see that (CLAUDE.md "Dev standards" mypy rule), as in seed.main.
    asyncio.run(run_agent(Settings()))  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
