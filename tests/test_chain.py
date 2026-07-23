"""Monday chain orchestration (docs/ARCHITECTURE.md / CLAUDE.md "Scheduling";
src/investment/chain.py). The sequential/abort/ErrorEvent contract with stub
steps against a real throwaway SQLite, and the DUE-ON-START arithmetic."""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import pytest

from investment import chain
from investment.db.sqlite import InvestmentDB


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "chain.db")
    yield conn
    await conn.close()


# -- DUE-ON-START ------------------------------------------------------------


def test_most_recent_monday_start() -> None:
    # Wed 2026-07-22 14:00 -> Monday 2026-07-20 08:00
    assert chain.most_recent_monday_start(datetime(2026, 7, 22, 14, 0)) == datetime(
        2026, 7, 20, 8, 0
    )
    # Monday 07-20 07:00 (before 08:00) -> the PREVIOUS Monday 07-13 08:00
    assert chain.most_recent_monday_start(datetime(2026, 7, 20, 7, 0)) == datetime(
        2026, 7, 13, 8, 0
    )


def test_is_chain_due() -> None:
    now = datetime(2026, 7, 22, 9, 0)  # Wednesday
    assert chain.is_chain_due(None, now) is True  # never run
    assert chain.is_chain_due(datetime(2026, 7, 20, 9, 0), now) is False  # ran this Monday
    assert chain.is_chain_due(datetime(2026, 7, 13, 9, 0), now) is True  # a Monday was missed


# -- the sequential runner ---------------------------------------------------


async def test_all_steps_run_in_order_on_success(db: InvestmentDB) -> None:
    order: list[str] = []

    def step(name: str) -> chain.ChainStep:
        async def _run() -> None:
            order.append(name)

        return (name, _run)

    result = await chain.run_chain(db, [step("a"), step("b"), step("c")], "run-1")
    assert result.ok is True
    assert result.completed == ["a", "b", "c"] == order
    assert result.failed_step is None


async def test_failure_aborts_the_rest_and_logs_an_error_event(db: InvestmentDB) -> None:
    ran: list[str] = []

    async def ok() -> None:
        ran.append("ok")

    async def boom() -> None:
        ran.append("boom")
        raise RuntimeError("regime step exploded")

    async def never() -> None:
        ran.append("never")

    result = await chain.run_chain(
        db, [("ok", ok), ("boom", boom), ("never", never)], "run-2"
    )
    assert result.ok is False
    assert result.failed_step == "boom"
    assert result.completed == ["ok"]  # only the step before the failure
    assert ran == ["ok", "boom"]  # 'never' did not run — the chain aborted
    assert "regime step exploded" in (result.error or "")

    # the abort is recorded in the EventLog (the audit trail even on failure)
    events = await db.query(
        "SELECT source_id, payload FROM event_log WHERE type = 'ErrorEvent'"
    )
    assert len(events) == 1
    assert events[0]["source_id"] == "run-2"
    assert "boom" in events[0]["payload"] and "RuntimeError" in events[0]["payload"]


async def test_an_empty_chain_is_a_clean_no_op(db: InvestmentDB) -> None:
    result = await chain.run_chain(db, [], "run-3")
    assert result.ok is True
    assert result.completed == []
