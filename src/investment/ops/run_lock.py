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
"""

import dataclasses
import logging
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

    def __init__(self) -> None:
        self._holder: Holder | None = None

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
        try:
            yield holder
        finally:
            # RELEASED ON FAILURE TOO. A chain that raises must not leave the
            # lock held: every later run would be refused with the name of an
            # operation that died hours ago, and the agent would go quiet in a
            # way that looks exactly like "nothing was due".
            self._holder = None
            logger.info("run-lock released by %s", name)
