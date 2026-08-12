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
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.error import TelegramError
from telegram.ext import Application

from investment.chain import CHAIN_START_HOUR, CHAIN_START_WEEKDAY
from investment.config import Settings
from investment.corpus.embedding import InProcessEmbedder
from investment.corpus.ingester import CorpusIngester
from investment.corpus.watcher import InboxWatcher
from investment.db.sqlite import InvestmentDB
from investment.ops.run_lock import RunLock
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
from investment.runtime import AgentRuntime
from investment.telegram.bot import build_application
from investment.watch.event_watch import build_triage_agent
from investment.weekly import chain_if_due
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
        logger.error(
            "TELEGRAM FRONT DISABLED (%s: %s). The agent runs and records normally; "
            "the digest and the alerts have nowhere to go until TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID are set in .env.",
            type(exc).__name__,
            exc,
        )
        return False
    return True


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

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # `add_signal_handler` rather than `signal.signal`: it runs the callback
        # ON the event loop, so setting the event cannot race with the tasks
        # waiting on it. The handler does nothing but set — the shutdown itself
        # is ordinary async code below, where a transaction can finish.
        loop.add_signal_handler(sig, stop.set)

    watcher = InboxWatcher(db, runtime.ingester, settings.inbox_path, settings.sources_path)
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
    scheduler.add_job(
        tick,
        CronTrigger(day_of_week=CHAIN_START_WEEKDAY, hour=CHAIN_START_HOUR, minute=0),
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
    both files and the split would only be a duplication."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

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
    asyncio.run(run_agent(Settings()))  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
