"""weekly chain orchestration (docs/ARCHITECTURE.md / CLAUDE.md "Scheduling":
the weekly chain is "strictly sequential, abort + Telegram alert on failure;
DUE-ON-START at launch/wake if the last success predates the most recent anchor
(Sunday 08:00)").

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

# WHEN THE WEEK'S WORK IS ANCHORED. `weekday()` numbering: Monday is 0, Sunday
# is 6 (owner decision 2026-08-12, moved from Monday).
#
# SUNDAY MORNING IS BEFORE THE WEEK OPENS, which is the whole reason: the digest
# carries an order the owner places by hand, and delivered Monday 09:30 it
# arrived after the European open. Nothing is lost on the data side — a Sunday
# catch-up fetches Friday's closes, and so did a weekly run 08:00 one.
#
# ONE CASE GETS WORSE, and it is stated rather than discovered later. The
# market-signal decision is taken on the first chain run AT OR AFTER the month's
# anchor (the first TRADING day). When that anchor falls on a weekly run — roughly
# one month in five — a weekly chain decided it the same morning and a Sunday
# chain decides it six days later. Every other month gains a day. The decision
# itself is still dated and priced on the anchor (ADR-003), so what shifts is
# when the owner is told, not what was decided.
CHAIN_START_WEEKDAY = 6
CHAIN_START_HOUR = 8  # 08:00 Europe/Zurich, at the presentation edge

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


def most_recent_chain_start(
    now: datetime, weekday: int = CHAIN_START_WEEKDAY, hour: int = CHAIN_START_HOUR
) -> datetime:
    """The most recent `weekday` `hour`:00 at or before `now`.

    `(now.weekday() - weekday) % 7` is how many days back the last anchor was —
    0 on the anchor day itself. If that lands in the future (the anchor day,
    before `hour`), step back a week. Written for any weekday rather than for
    Monday's `now.weekday()` shortcut, because the anchor moved once and a
    formula that only works for one day is a formula that has to be rewritten
    the next time."""
    anchor = (now - timedelta(days=(now.weekday() - weekday) % 7)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    if anchor > now:
        anchor -= timedelta(days=7)
    return anchor


def is_chain_due(
    last_success: datetime | None,
    now: datetime,
    *,
    weekday: int = CHAIN_START_WEEKDAY,
    hour: int = CHAIN_START_HOUR,
) -> bool:
    """DUE-ON-START (CLAUDE.md "Scheduling"): the chain is due at launch/wake if
    it has NEVER run, or its last success predates the most recent anchor — i.e.
    the anchor came and went while the laptop slept (ADR-002: no nightly cron,
    so the wake path must catch up).

    `weekday` AND `hour` ARE KEYWORD-ONLY, and the reason is a bug this
    signature already caused. `weekday` was inserted BEFORE `hour` when the
    anchor moved (2026-08-12) and every caller passing the hour positionally —
    `weekly.chain_if_due` did — silently began passing 8 as the WEEKDAY.
    `(now.weekday() - 8) % 7` is a perfectly good number, so nothing raised and
    the anchor was simply wrong. A test caught it within the minute; a
    positional call now cannot be written at all."""
    if last_success is None:
        return True
    return last_success < most_recent_chain_start(now, weekday, hour)


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
