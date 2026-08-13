"""The command layer (`ops/commands.py`, ADR-005, docs/TASKS.md Task 6ter.1).

Real throwaway SQLite, no fronts. What is pinned here is the three invariants
the layer exists for — idempotent across fronts, single-flight, EventLog first —
plus the validation of the one number a user can type that binds real gates.

The FRONTS are deliberately not tested through: they parse and render, and a
test that drove Telegram to assert a database row would be testing the wrong
thing (and would need a bot token). What matters is that they have nothing else
to call.
"""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from investment.db.sqlite import InvestmentDB
from investment.ops import commands as C
from investment.ops.run_lock import RunLock
from investment.runtime import AgentRuntime

TODAY = date(2026, 8, 12)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "commands.db")
    await conn.command(
        "INSERT INTO user_profile (user_id, currency, benchmark, max_drawdown_pct, "
        "max_single_asset_pct, phase, created_at, updated_at) VALUES ('u', 'USD', 'b', -25.0, "
        "50.0, 'accumulation', '2026-01-01', '2026-01-01')"
    )
    await conn.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'Four Seasons', 1, 't', '2026-01-01')"
    )
    await conn.command(
        "INSERT INTO strategy (id, title, description, framework_id, status, enabled, "
        "conviction, conditions, source, trace, created_at, updated_at) VALUES "
        "('momentum-macro', 'M', 'd', '4s', 'active', 1, 50, 'always', 'corpus', 't', "
        "'2026-01-01', '2026-01-01')"
    )
    yield conn
    await conn.close()


def _runtime(db: InvestmentDB, tmp_path: Path, lock: RunLock | None = None) -> AgentRuntime:
    """Only the components the commands under test actually touch. The heavy
    ones (agents, embedder, ingester) are never reached by a read or a rule
    change, and building them would need an API key and 90MB of model."""
    settings = SimpleNamespace(inbox_path=tmp_path / "inbox")
    return cast(
        AgentRuntime,
        SimpleNamespace(db=db, settings=settings, lock=lock or RunLock()),
    )


async def _decisions(db: InvestmentDB) -> list[dict[str, Any]]:
    return await db.query(
        "SELECT payload FROM event_log WHERE type = 'UserDecisionEvent' ORDER BY id"
    )


# -- invariant 1: idempotent across fronts ----------------------------------


async def test_disabling_a_strategy_records_one_decision(db: InvestmentDB, tmp_path: Path) -> None:
    runtime = _runtime(db, tmp_path)
    result = await C.set_strategy_enabled(runtime, "momentum-macro", enabled=False)

    assert result.ok and result.changed
    rows = await db.query("SELECT enabled FROM strategy WHERE id = 'momentum-macro'")
    assert not rows[0]["enabled"]
    assert len(await _decisions(db)) == 1


async def test_disabling_it_again_from_another_front_changes_nothing(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """THE INVARIANT. The owner disables on the dashboard, then types /disable
    on the phone: the second must read as a statement of fact and must NOT
    write a second decision into the audit trail as though they decided
    twice."""
    runtime = _runtime(db, tmp_path)
    await C.set_strategy_enabled(runtime, "momentum-macro", enabled=False)
    again = await C.set_strategy_enabled(runtime, "momentum-macro", enabled=False)

    assert again.ok and not again.changed
    assert "already disabled" in again.message
    assert len(await _decisions(db)) == 1  # still one


async def test_an_unknown_strategy_is_refused_with_where_to_look(
    db: InvestmentDB, tmp_path: Path
) -> None:
    result = await C.set_strategy_enabled(_runtime(db, tmp_path), "no-such", enabled=False)
    assert not result.ok and "/ranking" in result.message
    assert await _decisions(db) == []


# -- the binding rule -------------------------------------------------------


async def test_the_drawdown_rule_moves_and_is_audited(db: InvestmentDB, tmp_path: Path) -> None:
    runtime = _runtime(db, tmp_path)
    result = await C.set_max_drawdown(runtime, -20.0)

    assert result.ok and result.changed
    rows = await db.query("SELECT max_drawdown_pct FROM user_profile")
    assert float(rows[0]["max_drawdown_pct"]) == -20.0
    decisions = await _decisions(db)
    assert len(decisions) == 1
    assert '"from": -25.0' in str(decisions[0]["payload"])


@pytest.mark.parametrize("bad", [20.0, 0.0, -100.0, -250.0])
async def test_a_drawdown_outside_the_range_is_refused(
    db: InvestmentDB, tmp_path: Path, bad: float
) -> None:
    """This number binds real gates: it excludes portfolios from the defender
    role and arms the stack's drawdown alert. A positive one would exclude
    everything; a typo'd -250 would exclude nothing, forever, silently."""
    result = await C.set_max_drawdown(_runtime(db, tmp_path), bad)
    assert not result.ok
    rows = await db.query("SELECT max_drawdown_pct FROM user_profile")
    assert float(rows[0]["max_drawdown_pct"]) == -25.0  # untouched
    assert await _decisions(db) == []


async def test_setting_the_rule_it_already_has_is_a_no_op(db: InvestmentDB, tmp_path: Path) -> None:
    result = await C.set_max_drawdown(_runtime(db, tmp_path), -25.0)
    assert result.ok and not result.changed
    assert await _decisions(db) == []


@pytest.mark.parametrize(
    ("raw", "expected"), [("-20", -20.0), (" -20% ", -20.0), ("-12.5", -12.5), ("x", None)]
)
def test_a_percentage_typed_by_hand_is_tolerated(raw: str, expected: float | None) -> None:
    """The owner types `/drawdown -20%` as readily as `-20`."""
    assert C.parse_float(raw) == expected


# -- invariant 2: single-flight ---------------------------------------------


async def test_a_command_is_refused_while_something_heavy_runs(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """Refused with WHAT is running, never queued behind it: two chains in
    sequence would run the second on the artefacts of the first."""
    lock = RunLock()
    runtime = _runtime(db, tmp_path, lock)
    async with lock.hold("weekly-chain"):
        result = await C.refresh(runtime)
    assert not result.ok
    assert "weekly-chain" in result.message


async def test_the_ad_hoc_cycle_is_capped_per_day(db: InvestmentDB, tmp_path: Path) -> None:
    """UC9 allows at most one ad-hoc cycle a day, and the count comes from the
    JOURNAL — a process restart must not hand the owner a fresh allowance."""
    runtime = _runtime(db, tmp_path)
    async with db.transaction():
        await db.append_event(
            type="WorkerReadingEvent",
            source_uc="UC8",
            source_id=None,
            payload={"trigger": C.CYCLE_TRIGGER},
            event_date=TODAY,
        )

    assert await C.adhoc_cycles_today(runtime, TODAY) == 1
    result = await C.run_cycle(runtime, today=TODAY)
    assert not result.ok and "Already ran" in result.message


async def test_the_monday_chains_own_cycle_does_not_use_up_the_allowance(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """The chain journals its cycle under a different trigger, so a weekly run does
    not silently spend the day's ad-hoc budget."""
    runtime = _runtime(db, tmp_path)
    async with db.transaction():
        await db.append_event(
            type="WorkerReadingEvent",
            source_uc="UC8",
            source_id=None,
            payload={"trigger": "weekly-chain"},
            event_date=TODAY,
        )
    assert await C.adhoc_cycles_today(runtime, TODAY) == 0


# -- reads and the note channel ---------------------------------------------


async def test_status_reads_as_never_run_on_a_fresh_database(
    db: InvestmentDB, tmp_path: Path
) -> None:
    result = await C.status(_runtime(db, tmp_path))
    assert result.ok and not result.changed
    assert "last chain: never" in result.message
    assert "idle" in result.message


async def test_status_names_what_is_running(db: InvestmentDB, tmp_path: Path) -> None:
    lock = RunLock()
    runtime = _runtime(db, tmp_path, lock)
    async with lock.hold("weekly-chain"):
        result = await C.status(runtime)
    assert "weekly-chain since" in result.message


async def test_ranking_says_so_when_the_chain_has_not_run(db: InvestmentDB, tmp_path: Path) -> None:
    result = await C.ranking(_runtime(db, tmp_path))
    assert result.ok and not result.changed and "No ranking yet" in result.message


async def test_a_note_lands_in_the_inbox_for_the_watcher(db: InvestmentDB, tmp_path: Path) -> None:
    """Written to the inbox and NOT ingested here: a note is deposited by the
    owner, and the watcher's 5-minute quiet period exists so a burst settles
    into one batch. UC3's events are the opposite case and ingest immediately."""
    runtime = _runtime(db, tmp_path)
    result = await C.save_note(runtime, "  the curve steepened after the SNB presser  ")

    assert result.ok and result.changed
    notes = list((tmp_path / "inbox").glob("*-note.md"))
    assert len(notes) == 1
    assert notes[0].read_text().strip() == "the curve steepened after the SNB presser"
    assert await db.query("SELECT id FROM document") == []  # the watcher's job, not ours


async def test_two_notes_in_the_same_second_do_not_overwrite_each_other(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """Seconds are not a fine enough clock for a chat front. A thought typed in
    two halves — the ordinary way people write on a phone — produced one
    filename, and `write_text` silently replaced the first with the second. The
    quiet period then guarantees they were both still waiting to be ingested.
    `delivery.write_locally` reached the same answer for the same reason."""
    runtime = _runtime(db, tmp_path)
    await C.save_note(runtime, "the curve steepened")
    await C.save_note(runtime, "and the SNB said nothing")

    notes = sorted((tmp_path / "inbox").glob("*-note.md"))
    assert len(notes) == 2
    assert {n.read_text().strip() for n in notes} == {
        "the curve steepened",
        "and the SNB said nothing",
    }


# -- rendering --------------------------------------------------------------


def test_a_refusal_is_marked_and_a_no_op_does_not_read_as_an_action() -> None:
    assert C.describe_result(C.CommandResult.refused("nope")).startswith("⛔")
    assert C.describe_result(C.CommandResult.noop("already so")).startswith("(i)")
    assert C.describe_result(C.CommandResult(ok=True, message="done", changed=True)) == "done"
