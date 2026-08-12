"""The Monday chain — what runs, in what order, and when it is DUE
(docs/TASKS.md Phase 7; CLAUDE.md "Scheduling").

Split from `main.py` when the command layer became the chain's second caller:
the owner can ask for a chain from Telegram, the CLI or the dashboard
(docs/TASKS.md Task 6ter.1 lists `chain` among the four commands), and
`ops/commands.py` cannot import the process that imports it. The process owns
TIME — the cron, the heartbeat, the signals; this module owns the WORK.

DUE-ON-START lives here too, because "is the chain due" is a property of the
chain and its marker rather than of whichever trigger asked. Both triggers go
through `chain_if_due`, and the redundancy is safe because the marker
(`detector_state.last_chain_success`) and the run-lock each independently stop a
second run (main.py explains why there are two triggers at all).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

from ulid import ULID

from investment.chain import CHAIN_START_HOUR, ChainResult, ChainStep, is_chain_due, run_chain
from investment.corpus.curation_sweep import sweep_corpus
from investment.db.backup import backup_database
from investment.db.sqlite import InvestmentDB
from investment.decision_cycle import run_decision_cycle
from investment.delivery import deliver, outbox_path
from investment.market_signal_cycle import run_market_signal_cycle
from investment.mechanical.as_of_cycle import reweigh_invariants_asof
from investment.mechanical.backtests import run_backtests_and_favors
from investment.mechanical.catchup import run_catchup
from investment.mechanical.outcomes import evaluate_proposals, strategy_probation_check
from investment.mechanical.ratios import value_portfolios
from investment.mechanical.scenarios import warm_start_scenario_probabilities
from investment.mechanical.snapshots import build_snapshot
from investment.ops.run_lock import AlreadyRunning
from investment.runtime import AgentRuntime
from investment.telegram.digest import build_digest
from investment.watch.event_watch import flagged_message, run_event_watch

logger = logging.getLogger(__name__)

# The chain's own name in the run-lock. The other holders are the command
# layer's ({catchup, uc8} — ops/commands.py).
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
    runtime: AgentRuntime,
    *,
    thresholds: dict[str, float],
    user_profile: dict[str, Any],
    run_id: str,
    today: date,
    send: Callable[[str], Awaitable[bool]],
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
    db, settings = runtime.db, runtime.settings
    window = int(thresholds["rolling_window_days"])

    async def event_watch() -> None:
        """08:05. The flagged items are pushed to Telegram HERE rather than
        waiting for the digest: `needs-user-input` is a question to the owner,
        and a question that arrives four hours later inside a weekly report is
        not a question (UC3: "flagged and pushed to Telegram instead of being
        hallucinated")."""
        report = await run_event_watch(db, runtime.ingester, runtime.triage_agent)
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
            runtime.planner_pre,
            runtime.worker_agent,
            runtime.planner_post,
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
        ("curation", lambda: sweep_corpus(db, settings, embedder=runtime.embedder)),
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
    runtime: AgentRuntime, *, today: date | None = None
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
    db, settings = runtime.db, runtime.settings
    today = today or date.today()
    run_id = str(ULID())
    token, chat = settings.telegram_bot_token, settings.telegram_chat_id

    async def send(text: str) -> bool:
        """Deliver to the owner, Telegram first and a local file if that fails
        (`delivery.py`). Returns whether it went out over Telegram — the chain
        does not act on it either way, because a message written to the outbox
        has still been delivered."""
        channel = await deliver(
            text, token=token, chat_id=chat, outbox=outbox_path(settings.db_path)
        )
        return channel == "telegram"

    try:
        async with runtime.lock.hold(CHAIN_LOCK):
            thresholds = {
                str(r["key"]): float(r["value"])
                for r in await db.query("SELECT key, value FROM system_thresholds")
            }
            profile_rows = await db.query("SELECT * FROM user_profile LIMIT 1")
            if not profile_rows:
                raise RuntimeError("no user_profile row — run the seed first")

            steps = monday_steps(
                runtime,
                thresholds=thresholds,
                user_profile=dict(profile_rows[0]),
                run_id=run_id,
                today=today,
                send=send,
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


async def chain_if_due(runtime: AgentRuntime, *, now: datetime | None = None) -> ChainResult | None:
    """DUE-ON-START: run the chain iff its last success predates the most recent
    Monday 08:00 (chain.py `is_chain_due`). `None` when it is not due.

    The naive-vs-aware comparison is settled HERE: the marker is written in UTC
    and `is_chain_due` compares against a local Monday 08:00, so `now` must be
    local wall-clock and the marker is converted to it. Europe/Zurich lives at
    the presentation edge (CLAUDE.md), and "the most recent Monday 08:00" is a
    statement about the owner's calendar, not about UTC."""
    now = now or datetime.now().astimezone()
    last = await last_chain_success(runtime.db)
    if not is_chain_due(last.astimezone(now.tzinfo) if last else None, now, CHAIN_START_HOUR):
        return None
    logger.info("monday chain is DUE (last success: %s)", last)
    return await run_monday_chain(runtime, today=now.date())
