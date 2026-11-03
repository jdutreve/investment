"""The single-flight RUN-LOCK (docs/MILESTONES.md M9; docs/TASKS.md Task 6ter.1
"`ops/commands.py` invariants").

ONE lock shared by every heavy operation — {catchup, chain, uc8, replay} — so a
second request is REFUSED with what is already running rather than queued behind
it. The spec's own reason: "Covers the weekly chain vs manual runs vs the ad-hoc
UC9 UC8."

WHY REFUSE RATHER THAN WAIT. These operations are not independent units of work
that happen to share a database; they read and rewrite the same derived state.
Two chains queued back to back would run the second one on the artefacts of the
first and call the result a fresh Monday. Refusing says so out loud, to whichever
front asked, and leaves the running one alone.

WHY IT IS NOT `db.transaction()`. That serializes WRITES (ADR-004: one connection,
one writer). This serializes DECISIONS — a chain is dozens of transactions with
LLM calls and network fetches between them, and nothing about the write lock
stops a second chain interleaving with the first between two of them.

IN-PROCESS, deliberately, because the agent IS one process (ADR-002/ADR-004): the
scheduler, the watcher, the bot and the API all live inside it, which is exactly
the set of callers that can collide. A second `python -m investment.main` is a
different failure — two writers on one SQLite file — and belongs to whatever
guards the process itself, not here.

THE MAC STAYS AWAKE WHILE THE LOCK IS HELD (owner, 2026-11-02). An ad-hoc cycle
on 2026-11-01 lost its Planner's last call when the laptop idle-slept one minute
after the Worker answered: the request timed out across seven hours of sleep and
wake, and the cycle failed with its knowledge uncommitted. Holding the lock now
holds a `caffeinate -i` assertion (idle sleep only: closing the lid still sleeps
the Mac, ADR-002), released with the lock, and tied to the agent's pid so it
cannot outlive the agent.
"""

import asyncio
import dataclasses
import logging
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    """Raised by `RunLock.hold` when another operation holds the lock.

    Carries the holder and when it started, because "already running" alone
    sends the owner to the logs to find out what — and the answer is the whole
    point of refusing."""

    def __init__(self, holder: str, since: datetime) -> None:
        self.holder = holder
        self.since = since
        super().__init__(f"already running: {holder} (since {since.isoformat(timespec='seconds')})")


@dataclasses.dataclass(frozen=True)
class Holder:
    """Who holds the lock, and since when."""

    name: str
    since: datetime


class RunLock:
    """Non-blocking single-flight over the heavy operations.

    `asyncio.Lock` is the wrong primitive on its own: awaiting it is exactly the
    queueing this must not do, and `locked()` answers "is it held" without saying
    BY WHAT. So the state is a holder record guarded by the loop's own
    single-threadedness — the check and the set happen with no `await` between
    them, so no other task can interleave (the same argument
    `InvestmentDB._serialized` makes about its transaction guard)."""

    def __init__(self, *, keep_awake: bool = False) -> None:
        self._holder: Holder | None = None
        # The live agent only (`main.py`): tests and tools hold the lock
        # without touching the machine's power state.
        self._keep_awake = keep_awake

    @property
    def holder(self) -> Holder | None:
        """The running operation, or None. For `invest status` and the digest —
        a read, never a decision: acting on it would be a check-then-act race
        that `hold` exists to make impossible."""
        return self._holder

    @asynccontextmanager
    async def hold(self, name: str) -> AsyncIterator[Holder]:
        """Hold the lock for `name`, or raise `AlreadyRunning`.

        NOT re-entrant, and that is deliberate: the weekly chain calls UC8 as a
        step, so a re-entrant lock would let an ad-hoc UC8 slip in beside the
        chain's own and both would run. Compose by holding ONCE at the outermost
        operation — `run_chain` holds, its steps do not."""
        if self._holder is not None:
            raise AlreadyRunning(self._holder.name, self._holder.since)
        holder = Holder(name=name, since=datetime.now(UTC))
        self._holder = holder
        logger.info("run-lock acquired by %s", name)
        awake = await _prevent_idle_sleep(name) if self._keep_awake else None
        try:
            yield holder
        finally:
            if awake is not None:
                await _allow_idle_sleep(awake, name)
            # RELEASED ON FAILURE TOO. A chain that raises must not leave the
            # lock held: every later run would be refused with the name of an
            # operation that died hours ago, and the agent would go quiet in a
            # way that looks exactly like "nothing was due".
            self._holder = None
            logger.info("run-lock released by %s", name)


async def _prevent_idle_sleep(name: str) -> asyncio.subprocess.Process | None:
    """A `caffeinate -i` assertion for as long as `name` runs, or None where the
    command does not exist (not macOS). `-w` ends it with the agent's process
    if the release below never runs."""
    command = shutil.which("caffeinate")
    if command is None:
        logger.info("no caffeinate here: %s runs without an idle-sleep assertion", name)
        return None
    process = await asyncio.create_subprocess_exec(command, "-i", "-w", str(os.getpid()))
    logger.info("idle sleep prevented while %s runs (caffeinate pid %d)", name, process.pid)
    return process


async def _allow_idle_sleep(process: asyncio.subprocess.Process, name: str) -> None:
    if process.returncode is None:
        process.terminate()
        await process.wait()
    logger.info("idle sleep allowed again after %s", name)
