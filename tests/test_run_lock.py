"""The single-flight run-lock (`ops/run_lock.py`, docs/MILESTONES.md M9).

Every assertion here is about a COLLISION, which is the only thing this module
exists for: the Monday chain against a manual run, the cron against the
heartbeat, an ad-hoc UC9 cycle against the chain's own. A lock that merely
serialized them would satisfy none of these — the refusal, and what the refusal
SAYS, is the feature.
"""

import asyncio

import pytest

from investment.ops.run_lock import AlreadyRunning, RunLock


async def test_an_uncontended_hold_runs_and_releases() -> None:
    lock = RunLock()
    assert lock.holder is None
    async with lock.hold("chain") as holder:
        assert holder.name == "chain"
        assert lock.holder is not None and lock.holder.name == "chain"
    assert lock.holder is None


async def test_a_second_holder_is_refused_and_told_what_is_running() -> None:
    """ "Already running" alone sends the owner to the logs. The exception names
    the holder and when it started, because that is the answer they wanted."""
    lock = RunLock()
    async with lock.hold("monday-chain"):
        with pytest.raises(AlreadyRunning) as raised:
            async with lock.hold("uc8"):
                pytest.fail("the second holder must not run")
    assert raised.value.holder == "monday-chain"
    assert "monday-chain" in str(raised.value)
    assert raised.value.since.isoformat()[:4].isdigit()


async def test_the_lock_is_released_when_the_operation_raises() -> None:
    """The failure mode that would silence the agent: a chain that raises must
    not leave the lock held, or every later run is refused with the name of an
    operation that died hours ago — indistinguishable from "nothing was due"."""
    lock = RunLock()
    with pytest.raises(RuntimeError, match="step failed"):
        async with lock.hold("monday-chain"):
            raise RuntimeError("step failed")
    assert lock.holder is None
    async with lock.hold("uc8"):  # the next caller gets in
        pass


async def test_the_lock_is_not_re_entrant() -> None:
    """Deliberate: the Monday chain runs UC8 as a step, so a re-entrant lock
    would let an ad-hoc UC8 in beside the chain's own and both would run.
    Compose by holding once, at the outermost operation."""
    lock = RunLock()
    async with lock.hold("monday-chain"):
        with pytest.raises(AlreadyRunning):
            async with lock.hold("monday-chain"):
                pytest.fail("re-entry must be refused like any other collision")


async def test_a_concurrent_task_is_refused_rather_than_queued() -> None:
    """The property the whole module is for, exercised through the event loop
    rather than by inspection: the second task must come back REFUSED while the
    first is still working, not run after it. Queueing would hand the second
    chain the artefacts of the first and call the result a fresh Monday."""
    lock = RunLock()
    started = asyncio.Event()
    release = asyncio.Event()

    async def first() -> str:
        async with lock.hold("monday-chain"):
            started.set()
            await release.wait()
            return "first done"

    async def second() -> str:
        await started.wait()
        try:
            async with lock.hold("uc8"):
                return "second ran"
        except AlreadyRunning as exc:
            return f"refused: {exc.holder}"

    task = asyncio.create_task(first())
    assert await second() == "refused: monday-chain"
    release.set()
    assert await task == "first done"
