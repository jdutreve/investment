"""Monday chain orchestration (docs/ARCHITECTURE.md / CLAUDE.md "Scheduling":
the Monday chain is "strictly sequential, abort + Telegram alert on failure;
DUE-ON-START at launch/wake if the last success predates the most recent Monday
08:00").

This is the chain CONTRACT, scheduler-agnostic: `run_chain` runs an ordered
list of named steps, each starting only after the previous SUCCEEDS, and on the
first failure appends an ErrorEvent (EventLog) and aborts — no later step runs
on stale/half-computed state. The launchd wiring and the Telegram alert are M9
(docs/MILESTONES.md); the caller inspects the returned `ChainResult` and alerts.

Steps are thunks (`() -> Awaitable`) so the caller binds each job's own
arguments — the mechanical jobs (regime step, NAV, ranking, scenarios,
backtests, valuations, outcomes) and the UC8 cycle + digest all have different
signatures, and a thunk list is what lets one runner sequence them without
knowing any of them.
"""

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from investment.db.sqlite import InvestmentDB

logger = logging.getLogger(__name__)

ERROR_EVENT = "ErrorEvent"
CHAIN_START_HOUR = 8  # Monday 08:00 (Europe/Zurich at the presentation edge)

ChainStep = tuple[str, Callable[[], Awaitable[object]]]


@dataclasses.dataclass(frozen=True)
class ChainResult:
    """The outcome of one chain run. `failed_step` is None on full success;
    otherwise it names the FIRST step that raised and `completed` holds the
    steps that ran before it (docs/ARCHITECTURE.md: the chain's earlier
    mechanical steps stand)."""

    run_id: str
    completed: list[str]
    failed_step: str | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.failed_step is None


def most_recent_monday_start(now: datetime, hour: int = CHAIN_START_HOUR) -> datetime:
    """The most recent Monday `hour`:00 at or before `now`. `weekday()` is 0 for
    Monday, so subtracting it lands on this week's Monday; if that is still in
    the future (early Monday, before `hour`), step back a week."""
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    if monday > now:
        monday -= timedelta(days=7)
    return monday


def is_chain_due(
    last_success: datetime | None, now: datetime, hour: int = CHAIN_START_HOUR
) -> bool:
    """DUE-ON-START (CLAUDE.md "Scheduling"): the chain is due at launch/wake if
    it has NEVER run, or its last success predates the most recent Monday 08:00
    — i.e. a Monday came and went while the laptop slept (ADR-002: no nightly
    cron, so the wake path must catch up)."""
    if last_success is None:
        return True
    return last_success < most_recent_monday_start(now, hour)


async def run_chain(
    db: InvestmentDB, steps: list[ChainStep], run_id: str, *, source_uc: str = "chain"
) -> ChainResult:
    """Run `steps` in order, each after the previous succeeds. On the first
    exception: log it, append an ErrorEvent (its own transaction — EventLog is
    the audit trail even for a failure), and abort — later steps do NOT run.
    Returns a `ChainResult`; it never re-raises, so the scheduler stays alive to
    send the alert (M9) rather than crashing."""
    completed: list[str] = []
    for name, thunk in steps:
        try:
            await thunk()
        except Exception as exc:
            # A chain runner must RECORD and abort, not crash the scheduler —
            # the abort + ErrorEvent IS the handling (CLAUDE.md "no bare except";
            # this is a named catch that surfaces via the EventLog + return).
            logger.error("chain %s aborted at step %s: %s", run_id, name, exc, exc_info=True)
            async with db.transaction():
                await db.append_event(
                    type=ERROR_EVENT,
                    source_uc=source_uc,
                    source_id=run_id,
                    payload={"step": name, "error": str(exc), "error_type": type(exc).__name__},
                )
            return ChainResult(run_id, completed, name, str(exc))
        completed.append(name)
    return ChainResult(run_id, completed, None, None)
