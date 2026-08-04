"""Regression tests for InvestmentDB + the UC0 static seed (M1 scope).

Real SQLite files, no mocks (CLAUDE.md "Tests": this codebase's correctness
lives in real schema constraints — trace NOT NULL, FK edges, the defender
unique index — which mocks would hide). The one external-I/O boundary
(Yahoo/FRED, step 9 — M2) is not the DB; these M1-scope tests inject a
synthetic `fetch_raw` stub via `run_seed`'s injection point so a unit test
never makes a live network call (see tests/test_market.py for step 9 itself).
"""

import asyncio
import itertools
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from investment.config import Settings
from investment.db.schema import (
    DOCUMENT_TABLES,
    ENTITY_TABLES,
    RELATION_TABLES,
    TS_TABLES,
)
from investment.db.seed_data import (
    BACKED_BY_EDGES,
    DESIGNED_FOR_EDGES,
    FRAMEWORKS,
    HOLDS_EDGES,
    INVARIANTS,
    PORTFOLIOS,
    REGIME_TYPES,
    SCENARIOS,
    STRATEGIES,
)
from investment.db.sqlite import InvestmentDB
from investment.seed import run_seed


@pytest.fixture
async def db(tmp_path: Path):
    database = InvestmentDB(tmp_path / "test.db")
    yield database
    await database.close()


def _test_settings(tmp_path: Path) -> Settings:
    """Settings with dummy keys and a throwaway DB — bypasses .env."""
    return Settings(
        _env_file=None,
        anthropic_api_key="test",
        openrouter_api_key="test",
        fred_api_key="test",
        telegram_bot_token="test",
        telegram_chat_id="test",
        db_path=tmp_path / "seed.db",
        inbox_path=tmp_path / "inbox",
        sources_path=tmp_path / "sources",
    )  # type: ignore[call-arg]


async def _stub_fetch_raw(ticker_row: Any, api_key: str, start: date | None) -> pd.Series:
    """Synthetic ~2y daily series — enough for splice.py's 1y-overlap floor —
    identical shape for every ticker/proxy so this M1-scope suite never
    touches the network (M2 step 9 itself is exercised in test_market.py)."""
    dates = pd.date_range(end=date.today() - timedelta(days=1), periods=800, freq="D")
    return pd.Series(100.0 + 0.01 * range(800), index=dates)


async def test_schema_creates_all_33_tables(db: InvestmentDB) -> None:
    rows = await db.query("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in rows}
    expected = ENTITY_TABLES | RELATION_TABLES | TS_TABLES | DOCUMENT_TABLES
    assert expected <= tables
    # 31 in the spec + `curated_passage` (M7 curation checkpoint) + `proposal_cites`
    # (M8 reallocation citations, for the source='proposal' confrontations).
    assert len(expected) == 33


async def test_trace_mandatory_on_vertices(db: InvestmentDB) -> None:
    with pytest.raises(ValueError, match="trace mandatory"):
        await db.create_vertex("framework", {"id": "x", "name": "x", "enabled": True})
    # TRACE_EXEMPT tables accept a missing trace (parent framework first — FK)
    await db.upsert_vertex("framework", "4seasons", {"name": "f", "enabled": True, "trace": "t"})
    await db.upsert_vertex(
        "regime_type",
        "uncertain",
        {"name": "Uncertain", "framework_id": "4seasons", "description": "d"},
    )


async def test_transaction_rolls_back_event_and_vertex_atomically(db: InvestmentDB) -> None:
    """The EventLog append-before-commit invariant is atomic: a failure after
    the append must roll the event back too (CLAUDE.md 'EventLog')."""
    await db.upsert_vertex("framework", "4seasons", {"name": "f", "enabled": True, "trace": "t"})
    with pytest.raises(Exception, match="UNIQUE"):
        async with db.transaction():
            await db.append_event("SeedEvent", "UC0", None, {"k": 1})
            await db.create_vertex(
                "framework", {"id": "4seasons", "name": "dup", "enabled": True, "trace": "t"}
            )
    rows = await db.query("SELECT COUNT(*) AS n FROM event_log")
    assert rows[0]["n"] == 0


async def test_upsert_preserves_created_at_and_advances_updated_at(db: InvestmentDB) -> None:
    props = {
        "title": "t",
        "description": "v1",
        "source": "s",
        "status": "proposed",
        "weight_initial": 0.8,
        "floor_weight": 0.4,
        "trace": "t",
    }
    await db.upsert_vertex("invariant", "i1", props)
    first = (await db.query("SELECT created_at, updated_at FROM invariant WHERE id='i1'"))[0]
    await db.upsert_vertex("invariant", "i1", {**props, "description": "v2"})
    second = (
        await db.query("SELECT created_at, updated_at, description FROM invariant WHERE id='i1'")
    )[0]
    assert second["description"] == "v2"
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] >= first["updated_at"]


async def test_event_ids_strictly_increasing(db: InvestmentDB) -> None:
    """EventLog id = monotonic ULID = canonical append order
    (DATA_MODELS.md 'Ordering semantics')."""
    ids = [await db.append_event("SeedEvent", "UC0", None, {"i": i}) for i in range(500)]
    assert all(b > a for a, b in itertools.pairwise(ids))


async def test_append_ts_idempotent(db: InvestmentDB) -> None:
    ts = datetime(2026, 1, 5, tzinfo=UTC)
    tags = {"ticker": "SPY", "asset_class": "US_EQUITY", "currency": "USD"}
    await db.append_ts("market_data", ts, tags, {"level": 500.0})
    await db.append_ts("market_data", ts, tags, {"level": 501.0})
    rows = await db.query("SELECT level FROM market_data WHERE ticker='SPY'")
    assert len(rows) == 1
    assert rows[0]["level"] == 501.0


async def test_single_defender_enforced_by_db(db: InvestmentDB) -> None:
    await db.upsert_vertex("framework", "4s", {"name": "f", "enabled": True, "trace": "t"})
    common = {
        "name": "p",
        "framework_id": "4s",
        "enabled": True,
        "currency": "CHF",
        "benchmark": "aw",
        "allocation": {"SPY": 100},
        "max_drawdown_rule": -15.0,
        "max_single_asset_pct": 40.0,
        "phase": "accumulation",
        "trace": "t",
    }
    await db.upsert_vertex("portfolio", "p1", {**common, "defender": True})
    with pytest.raises(Exception, match="UNIQUE"):
        await db.upsert_vertex("portfolio", "p2", {**common, "defender": True})


async def test_seed_idempotent_two_runs(tmp_path: Path) -> None:
    """M1 Definition of Verified: re-run seed → zero duplicates, 2 SeedEvents."""
    settings = _test_settings(tmp_path)
    await run_seed(settings, fetch_raw=_stub_fetch_raw, yahoo_rate_limit_seconds=0.0)
    await run_seed(settings, fetch_raw=_stub_fetch_raw, yahoo_rate_limit_seconds=0.0)

    db = InvestmentDB(settings.db_path)
    try:
        counts = {
            table: (await db.query(f"SELECT COUNT(*) AS n FROM {table}"))[0]["n"]
            for table in (
                "framework",
                "regime_type",
                "invariant",
                "strategy",
                "scenario",
                "portfolio",
                "backed_by",
                "holds",
                "designed_for",
            )
        }
        # Counted from the seed constants, not frozen literals: the point of
        # this test is that a SECOND run duplicates nothing, which is exactly
        # as true with 6 seed invariants as with 7 — hardcoding the inventory
        # only makes it fail whenever the philosophy gains an entry.
        assert counts == {
            "framework": len(FRAMEWORKS),
            "regime_type": len(REGIME_TYPES),
            "invariant": len(INVARIANTS),
            "strategy": len(STRATEGIES),
            "scenario": len(SCENARIOS),
            "portfolio": len(PORTFOLIOS),
            "backed_by": len(BACKED_BY_EDGES),
            "holds": len(HOLDS_EDGES),
            "designed_for": len(DESIGNED_FOR_EDGES),
        }
        events = await db.query("SELECT COUNT(*) AS n FROM event_log WHERE type='SeedEvent'")
        assert events[0]["n"] == 2
        defenders = await db.query("SELECT COUNT(*) AS n FROM portfolio WHERE defender=1")
        assert defenders[0]["n"] == 1
    finally:
        await db.close()


async def test_seed_allocations_respect_binding_caps(tmp_path: Path) -> None:
    """Every seeded portfolio/scenario allocation sums to 100 and respects
    the binding 40% single-asset cap (REVISION_NOTES 'Risk rules')."""
    import json

    settings = _test_settings(tmp_path)
    await run_seed(settings, fetch_raw=_stub_fetch_raw, yahoo_rate_limit_seconds=0.0)
    db = InvestmentDB(settings.db_path)
    try:
        for row in await db.query("SELECT id, allocation, max_single_asset_pct FROM portfolio"):
            allocation = json.loads(row["allocation"])
            assert abs(sum(allocation.values()) - 100) < 1e-9, row["id"]
            assert max(allocation.values()) <= row["max_single_asset_pct"], row["id"]
        for row in await db.query("SELECT id, target_allocation FROM scenario"):
            allocation = json.loads(row["target_allocation"])
            assert abs(sum(allocation.values()) - 100) < 1e-9, row["id"]
            assert max(allocation.values()) <= 40.0, row["id"]
        probability_sums = await db.query(
            "SELECT strategy_id, SUM(probability) AS p FROM scenario GROUP BY strategy_id"
        )
        for row in probability_sums:
            assert abs(row["p"] - 100) < 1e-9, row["strategy_id"]
    finally:
        await db.close()


# -- transaction serialization (module docstring "TRANSACTION granularity") --


async def test_a_concurrent_write_does_not_land_inside_an_open_transaction(
    tmp_path: Path,
) -> None:
    """The atomicity `transaction()` exists for is only real if no other task
    can write into the open BEGIN. Before the lock, the intruder's row was part
    of the transaction and the ROLLBACK took it with it."""
    db = InvestmentDB(tmp_path / "tx.db")
    try:
        started = asyncio.Event()

        async def rolls_back() -> None:
            with pytest.raises(RuntimeError, match="deliberate"):
                async with db.transaction() as tx:
                    await tx.append_event(
                        type="T", source_uc="UC0", source_id=None, payload={"who": "owner"}
                    )
                    started.set()
                    await asyncio.sleep(0.05)  # the window another task could write into
                    raise RuntimeError("deliberate")

        async def intruder() -> None:
            await started.wait()
            await db.append_event(
                type="T", source_uc="UC0", source_id=None, payload={"who": "intruder"}
            )

        await asyncio.gather(rolls_back(), intruder())

        rows = await db.query(
            "SELECT json_extract(payload, '$.who') AS who FROM event_log ORDER BY id"
        )
        # the owner's append is gone with its rollback; the intruder's survives
        assert [r["who"] for r in rows] == ["intruder"]
    finally:
        await db.close()


async def test_two_concurrent_transactions_serialize_instead_of_colliding(
    tmp_path: Path,
) -> None:
    """A second BEGIN on the same connection raises "cannot start a transaction
    within a transaction", and its COMMIT would end the first one's unit early."""
    db = InvestmentDB(tmp_path / "tx2.db")
    try:

        async def one(who: str) -> None:
            async with db.transaction() as tx:
                await tx.append_event(
                    type="T", source_uc="UC0", source_id=None, payload={"who": who}
                )
                await asyncio.sleep(0.02)
                await tx.append_event(
                    type="T", source_uc="UC0", source_id=None, payload={"who": who}
                )

        await asyncio.gather(one("a"), one("b"))
        rows = await db.query(
            "SELECT json_extract(payload, '$.who') AS who FROM event_log ORDER BY id"
        )
        # each transaction's two appends are ADJACENT — neither interleaved
        assert [r["who"] for r in rows] in (["a", "a", "b", "b"], ["b", "b", "a", "a"])
    finally:
        await db.close()


async def test_nesting_a_transaction_is_refused_at_the_nested_with(tmp_path: Path) -> None:
    """Same-task re-entry is waved through by the owner check, so without this
    guard it would fail several statements later inside sqlite."""
    db = InvestmentDB(tmp_path / "tx3.db")
    try:
        with pytest.raises(RuntimeError, match="nested transaction"):
            async with db.transaction():
                async with db.transaction():
                    pass
    finally:
        await db.close()


async def test_a_concurrent_read_does_not_see_a_transaction_that_rolls_back(
    tmp_path: Path,
) -> None:
    """With ONE connection (ADR-004) a reader sits INSIDE an open transaction,
    so it reads uncommitted rows — and on a rollback it has read state that
    never existed. Reads are serialized behind the transaction for this."""
    db = InvestmentDB(tmp_path / "tx4.db")
    try:
        started = asyncio.Event()
        seen: list[int] = []

        async def rolls_back() -> None:
            with pytest.raises(RuntimeError, match="deliberate"):
                async with db.transaction() as tx:
                    await tx.append_event(
                        type="T", source_uc="UC0", source_id=None, payload={"phantom": True}
                    )
                    started.set()
                    await asyncio.sleep(0.05)
                    raise RuntimeError("deliberate")

        async def reader() -> None:
            await started.wait()
            rows = await db.query("SELECT COUNT(*) AS n FROM event_log")
            seen.append(int(rows[0]["n"]))

        await asyncio.gather(rolls_back(), reader())
        assert seen == [0]  # the phantom row was never observable
    finally:
        await db.close()
