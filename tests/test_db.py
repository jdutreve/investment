"""Regression tests for InvestmentDB + the UC0 static seed (M1 scope).

Real SQLite files, no mocks (CLAUDE.md "Tests": this codebase's correctness
lives in real schema constraints — trace NOT NULL, FK edges, the defender
unique index — which mocks would hide). The one external-I/O boundary
(Yahoo/FRED, step 9 — M2) is not the DB; these M1-scope tests inject a
synthetic `fetch_raw` stub via `run_seed`'s injection point so a unit test
never makes a live network call (see tests/test_market.py for step 9 itself).
"""

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
