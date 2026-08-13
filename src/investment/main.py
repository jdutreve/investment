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
     batch (corpus/watcher.py), and on a batch that produced documents:
     curation, a backup and a message to the owner (`after_ingestion`).
     Event-driven, not scheduled.
  1b. the TELEGRAM BOT   — the owner's front, polling for updates. Every handler
     dispatches to `ops/commands.py`, which is also what the CLI and the
     dashboard will call (ADR-005): one command layer, three fronts.
  2. the WEEKLY CRON    — Sunday 08:00 Europe/Zurich, the normal path
     (`chain.CHAIN_START_WEEKDAY`; moved from Monday by owner decision
     2026-08-12 so the digest lands before the week opens).
  3. the HEARTBEAT      — every 5 minutes, asks whether the chain is DUE. This
     is the wake path (ADR-002: the laptop sleeps, so a cron that fires at 08:00
     on a closed lid never fires at all), and also the launch path.
  4. SHUTDOWN           — SIGTERM/SIGINT finish the current transaction,
     checkpoint the WAL and close (CLAUDE.md "Dev standards").

WHY THE CRON AND THE HEARTBEAT ARE BOTH HERE, given they can both fire on the
same morning: they answer different questions. The cron is punctual and the
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
import fcntl
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.error import TelegramError
from telegram.ext import Application

from investment.chain import CHAIN_START_HOUR, CHAIN_START_WEEKDAY
from investment.config import Settings
from investment.corpus.curation_sweep import sweep_corpus
from investment.corpus.embedding import InProcessEmbedder
from investment.corpus.ingester import CorpusIngester, IngestResult
from investment.corpus.watcher import InboxWatcher
from investment.db.backup import backup_database
from investment.db.sqlite import InvestmentDB
from investment.delivery import deliver, outbox_path
from investment.ops.run_lock import AlreadyRunning, RunLock
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
from investment.redact import RedactingFormatter, redact_exception
from investment.runtime import AgentRuntime
from investment.telegram.bot import build_application
from investment.watch.event_watch import build_triage_agent
from investment.weekly import agent_now, chain_if_due
from investment.worker.agent import build_worker_agent

logger = logging.getLogger(__name__)

# How often the wake/launch path asks whether the chain is due. Five minutes is
# the spec's own figure (Phase 7) and the reasoning is asymmetric: a late chain
# costs the owner a few minutes of a weekly report, while a tight loop costs a
# wakeup on a laptop that is asleep most of the time. It is also short enough
# that "I opened the lid on Tuesday" produces the weekly chain before coffee.
HEARTBEAT_SECONDS = 300.0

# The chain's own name in the run-lock. The other holders arrive with the
# command layer ({catchup, uc8, replay} — Task 6ter.1).
CHAIN_LOCK = "weekly-chain"

# The post-ingestion curation's name in the same lock. It is the one holder that
# is triggered by a FILE rather than by a clock or a command, so it is also the
# one most likely to arrive while something else is running.
CURATION_LOCK = "ingestion-curation"


async def heartbeat(stop: asyncio.Event, tick: Callable[[], Awaitable[Any]]) -> None:
    """Call `tick` every `HEARTBEAT_SECONDS` until `stop`.

    Waiting on the stop event with a timeout rather than sleeping, so shutdown
    is immediate instead of up to five minutes late — the same shape
    `InboxWatcher.run` uses, for the same reason.

    A tick that RAISES must not kill the heartbeat: it is the wake path, and a
    process that stops asking whether the chain is due looks exactly like a week
    with nothing to do. `run_weekly_chain` already absorbs chain failures, so
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


async def _start_bot(bot: Application) -> bool:  # type: ignore[type-arg]
    """Start the Telegram front. Returns whether it is running.

    A bad or placeholder token is a CONFIGURATION problem with one front, and
    the agent's own work — decide, record, measure — does not depend on it. So
    it is logged with what to fix and the process carries on: the chain still
    runs, everything still lands in the database, and `invest status` still
    answers. What the owner loses until the token is set is delivery — the
    digest and the alerts — which is exactly what the log line says."""
    try:
        await bot.initialize()
        await bot.start()
        if bot.updater is not None:
            await bot.updater.start_polling()
    except TelegramError as exc:
        # REDACTED AT THE CALL SITE as well as by the formatter: `InvalidToken`
        # is built from the token it rejected, and this is the one line in the
        # system guaranteed to print it. The formatter is the net; a message
        # this predictable should not depend on the net (`investment/redact.py`).
        logger.error(
            "TELEGRAM FRONT DISABLED (%s: %s). The agent runs and records normally; "
            "the digest and the alerts have nowhere to go until TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID are set in .env.",
            type(exc).__name__,
            redact_exception(exc),
        )
        return False
    return True


@contextlib.contextmanager
def single_process(db_path: Path) -> Iterator[None]:
    """Refuse to start if another agent already holds this database.

    ADR-004 says the agent is the SOLE WRITER and `ops/run_lock.py` enforces
    single-flight INSIDE the process — its docstring is explicit that a second
    `python -m investment.main` is "a different failure ... and belongs to
    whatever guards the process itself, not here". Nothing guarded it. A manual
    launch beside the launchd one gave two schedulers, two heartbeats, two
    inbox watchers racing for the same files and two run-locks that know nothing
    of each other, each convinced it was alone.

    `flock` on a file beside the database, held for the process's whole life and
    released by the OS however it dies — which is the property a PID file does
    not have: a killed agent leaves a stale PID behind and the next launch has
    to guess whether it means anything."""
    lock_path = db_path.parent / f"{db_path.name}.agent.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                f"another agent is already running on {db_path} (lock: {lock_path}). "
                "Two agents on one SQLite file are two writers, two schedulers and two "
                "inbox watchers — stop the other one, or `launchctl unload` the LaunchAgent."
            ) from exc
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        yield
    finally:
        handle.close()


async def run_agent(settings: Settings) -> None:
    """Wire everything and run until a signal arrives."""
    db = InvestmentDB(settings.db_path)
    if not await db.query("SELECT 1 FROM framework WHERE id = '4seasons'"):
        await db.close()
        raise RuntimeError("seed not run — execute `uv run python -m investment.seed` first")

    embedder = InProcessEmbedder(settings.embedding_model)
    runtime = AgentRuntime(
        db=db,
        settings=settings,
        lock=RunLock(),
        planner_pre=PlannerPre(db, embedder, settings.planner_model, settings.openrouter_api_key),
        worker_agent=build_worker_agent(db, settings.worker_model, settings.openrouter_api_key),
        planner_post=PlannerPost(settings.planner_model, settings.openrouter_api_key),
        embedder=embedder,
        # ONE ingester, shared by the inbox watcher and the event watch: both
        # turn text into Documents, and `from_db` reads the calibrated chunking
        # and similarity thresholds out of `system_thresholds` once.
        ingester=await CorpusIngester.from_db(db, embedder),
        # The UC3 triage runs on the PLANNER model at the curator's effort: a
        # short structured judgement over one press item, the same shape of task
        # the curator does, and not the Worker's deliberation.
        triage_agent=build_triage_agent(
            settings.planner_model, settings.openrouter_api_key, settings.curator_reasoning_effort
        ),
    )

    async def tick() -> None:
        await chain_if_due(runtime)

    async def after_ingestion(results: list[IngestResult]) -> None:
        """CURATE, BACK UP, TELL THE OWNER — the event-driven half of the
        system, which M9 specifies and nothing called.

        `InboxWatcher` has taken a `curator` hook since Task 3.1 and this
        wiring never passed one, so a deposited book was ingested within the
        minute and then sat uncurated until the following Sunday's 08:10 sweep.
        M9's Definition of Verified is "deposit -> candidates on Telegram within
        ~5 min", and no code path could produce that. The backup after every
        ingestion batch (docs/TASKS.md Phase 7, `db/backup.py`'s own docstring:
        "the data changes through exactly two paths") was missing for the same
        reason — the chain backed itself up, the batch did not.

        THE SWEEP, not a direct curator call: it is checkpoint-guarded
        (`curated_passage`), so it curates exactly the passages this batch added
        and costs one query per untouched document. That also makes this hook
        and the Sunday step THE SAME operation — one of them failing is retried
        by the other, which is what `curation_sweep` means by resumable.

        UNDER THE RUN-LOCK, and refused rather than queued if the chain holds
        it: a sweep running beside the chain's own would pay for the same
        passages twice. Refused here means the 08:10 step does it.

        NOTHING RAISES OUT OF HERE. The files are already ingested and moved by
        the time this runs; a failed curation must not turn a successful batch
        into a quarantined one, and the watcher's loop must survive it.

        THE BACKUP IS NOT THE CURATION'S DEPENDANT — the two steps are
        sequential and independently guarded, which they were not. Both once sat
        in one `try` under one lock, so a curation that failed (or merely found
        the chain holding the lock) skipped the backup of an ingestion that had
        ALREADY COMMITTED. That inverts what the backup is for: the batch most
        worth having a copy of is precisely the one whose follow-up broke. The
        backup also needs no lock — SQLite's online backup API reads, and
        `backup_database` publishes atomically."""
        sweep = None
        try:
            async with runtime.lock.hold(CURATION_LOCK):
                sweep = await sweep_corpus(db, settings, embedder=runtime.embedder)
        except AlreadyRunning as exc:
            logger.info("post-ingestion curation deferred to the weekly sweep: %s", exc)
        except Exception:
            logger.exception("post-ingestion curation failed; the weekly sweep will retry it")

        try:
            await asyncio.to_thread(
                backup_database,
                settings.db_path,
                settings.db_path.parent / "backups",
                # `settings.tz`, not the machine's — the backup is named for a
                # day, and which day that is belongs to the owner's calendar
                # (`weekly.agent_now`). The weekly chain already passes its own
                # `today` for the same reason.
                today=agent_now(settings).date(),
            )
        except Exception:
            logger.exception("post-ingestion backup failed; the next chain will take one")

        if sweep is None:  # curation deferred or failed: nothing to report yet
            return
        titles = ", ".join(r.title for r in results)
        await deliver(
            f"📥 Ingested {len(results)} document(s): {titles}.\n"
            f"Curation produced {sweep.candidates} invariant candidate(s) "
            f"across {sweep.documents} document(s)."
            + (f"\n⚠️ {len(sweep.failed)} document(s) failed curation." if sweep.failed else ""),
            token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            outbox=outbox_path(settings.db_path),
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # `add_signal_handler` rather than `signal.signal`: it runs the callback
        # ON the event loop, so setting the event cannot race with the tasks
        # waiting on it. The handler does nothing but set — the shutdown itself
        # is ordinary async code below, where a transaction can finish.
        loop.add_signal_handler(sig, stop.set)

    watcher = InboxWatcher(
        db,
        runtime.ingester,
        settings.inbox_path,
        settings.sources_path,
        curator=after_ingestion,
    )
    # The bot runs INSIDE this process and shares the runtime — the same
    # connection (ADR-004), the same lock, the same agents. A separate bot
    # process would be a second writer on one SQLite file and a second run-lock
    # that knows nothing about the first.
    #
    # A FRONT THAT CANNOT START MUST NOT TAKE THE AGENT DOWN. Measured on the
    # first real launch (2026-08-12): `.env` still carried the placeholder
    # token, `bot.initialize()` raised `InvalidToken`, and the process died
    # before the chain had refreshed a single price. The market data, the regime
    # detection, the allocation decision and the outcome scoring do not depend
    # on Telegram, and `telegram/notify.py` already states the principle for the
    # other half of this channel — "failing to notify must not fail the caller",
    # because a raise there replaces a reported failure with an unreported one.
    # The receive half had the opposite behaviour, which was an inconsistency
    # rather than a decision.
    bot = build_application(runtime)
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    # The punctual path. Its target is the same `tick` the heartbeat calls, so
    # whichever arrives first runs the chain and the other finds it not due.
    # The day comes from `chain.CHAIN_START_WEEKDAY` rather than a literal, so
    # the cron and `is_chain_due` cannot disagree about which day the week turns
    # on — a cron firing on a day the due-check does not recognise would run
    # nothing, silently, for as long as nobody looked. APScheduler's
    # `day_of_week` counts Monday as 0 like `date.weekday()`, so the constant
    # passes straight through.
    #
    # THE TIMEZONE IS PASSED TO THE TRIGGER, not merely to the scheduler, and
    # the two are not the same thing. `AsyncIOScheduler(timezone=...)` only
    # reaches a trigger the SCHEDULER builds — the `add_job(func, "cron", ...)`
    # string form. A trigger constructed by the caller resolves its own
    # timezone in `__init__`, and with none given it takes
    # `tzlocal.get_localzone()` — the MACHINE's. Verified on APScheduler 3.11.3:
    # with the machine on Asia/Tokyo and the scheduler on Europe/Zurich, this
    # trigger reported Asia/Tokyo until the argument below was added. On this
    # laptop the two agree and the bug is invisible; on a trip they do not, and
    # the chain would fire at 08:00 of wherever the owner happens to be while
    # `is_chain_due` (which reads `settings.tz` through `weekly.agent_now`)
    # still thought Zurich. Both halves must anchor on the same clock.
    scheduler.add_job(
        tick,
        CronTrigger(
            day_of_week=CHAIN_START_WEEKDAY,
            hour=CHAIN_START_HOUR,
            minute=0,
            timezone=ZoneInfo(settings.tz),
        ),
    )

    tasks = [
        asyncio.create_task(watcher.run(stop), name="inbox-watcher"),
        asyncio.create_task(heartbeat(stop, tick), name="heartbeat"),
    ]
    scheduler.start()
    bot_running = await _start_bot(bot)
    logger.info(
        "agent up: db=%s tz=%s inbox=%s (chain due-on-start check running)",
        settings.db_path,
        settings.tz,
        settings.inbox_path,
    )
    try:
        # DUE-ON-START, before waiting: a laptop opened on Tuesday must not wait
        # five minutes for the heartbeat to notice that the anchor came and went.
        await tick()
        await stop.wait()
    finally:
        logger.info("shutting down")
        if bot_running:
            if bot.updater is not None:
                await bot.updater.stop()
            await bot.stop()
            await bot.shutdown()
        scheduler.shutdown(wait=False)
        stop.set()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await db.close()


class _BelowWarning(logging.Filter):
    """Lets INFO and DEBUG through, stops WARNING and above."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.WARNING


def configure_logging() -> None:
    """INFO to stdout, WARNING and above to stderr.

    `basicConfig` sends EVERYTHING to stderr, and under launchd that means the
    whole journal lands in `agent.error.log` while `agent.log` stays empty — a
    file named "error" holding a normal Sunday, and a file named for the log
    holding nothing. Both names then lie, and the one an owner opens first is
    the wrong one.

    The stdout handler needs the FILTER as well as its level: a level admits
    everything above it, so without the filter every warning would appear in
    both files and the split would only be a duplication.

    BOTH handlers redact (`investment/redact.py`). A rejected Telegram token and
    a failing FRED URL both carry their credential inside the exception text,
    and under launchd these two files are where that text lands and stays."""
    fmt = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    out = logging.StreamHandler(sys.stdout)
    out.setLevel(logging.INFO)
    out.addFilter(_BelowWarning())
    out.setFormatter(fmt)

    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    err.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [out, err]


def main() -> None:
    configure_logging()
    # pydantic-settings populates required fields from .env at runtime; mypy
    # cannot see that (CLAUDE.md "Dev standards" mypy rule), as in seed.main.
    settings = Settings()  # type: ignore[call-arg]
    # The lock is taken BEFORE the connection is opened, so the second agent
    # never touches the file at all.
    with single_process(settings.db_path):
        asyncio.run(run_agent(settings))


if __name__ == "__main__":
    main()
