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
import sqlite3
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
    BENCHMARK_PORTFOLIOS,
    DESIGNED_FOR_EDGES,
    FRAMEWORKS,
    HOLDS_EDGES,
    INVARIANTS,
    PORTFOLIOS,
    REGIME_TYPES,
    SCENARIOS,
    STRATEGIES,
    benchmarks_first,
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
        # Required, no default: config.py refuses to guess which model runs.
        planner_model="test/planner",
        worker_model="test/worker",
        openrouter_api_key="test",
        fred_api_key="test",
        telegram_bot_token="test",
        telegram_chat_id="test",
        gmail_address="test",
        gmail_app_password="test",
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


async def test_schema_declares_every_table_it_creates(db: InvestmentDB) -> None:
    """The registry and the DDL are ONE fact, checked in BOTH directions.

    `expected <= tables` alone only catches a declared table the DDL forgot to
    create — never the reverse, which is what actually happened: `innovation`
    and `revision_measurement` shipped in the DDL on 2026-08-11 and reached
    neither `DOCUMENT_TABLES` nor the count below, so `_VALID_TABLES` did not
    know about two live tables and this test stayed green throughout.
    """
    rows = await db.query("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in rows if not row["name"].startswith("sqlite_")}
    expected = ENTITY_TABLES | RELATION_TABLES | TS_TABLES | DOCUMENT_TABLES
    assert expected == tables
    # 31 in the spec + `curated_passage` (M7 curation checkpoint) + `proposal_cites`
    # (M8 reallocation citations, for the source='proposal' confrontations)
    # + `innovation` (the recurrence ledger) + `revision_measurement` (the
    # measured-verdict ledger), both 2026-08-11.
    assert len(expected) == 35


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


def test_benchmarks_are_built_before_the_portfolios_that_measure_against_them() -> None:
    """`benchmarks_first` carries an ORDERING CONSTRAINT, not a preference:
    `ratios._vs_benchmark` reads All Weather's persisted NAV back to compute
    every other portfolio's delta, so a run that built the others first would
    write a column of nulls on a fresh database.

    Both NAV builders walk the list through here, which is what let All Weather
    stop being an out-of-loop special case in each of them (seed step 12 and
    `catchup.refresh_nav` both did it, three lines under a docstring calling a
    second producer of one series a smell)."""
    ordered = benchmarks_first(PORTFOLIOS)

    assert {str(p["id"]) for p in ordered} == {str(p["id"]) for p in PORTFOLIOS}
    leading = [str(p["id"]) for p in ordered[: len(BENCHMARK_PORTFOLIOS)]]
    assert set(leading) == BENCHMARK_PORTFOLIOS
    # Everything else keeps the order it was seeded in — the function moves the
    # benchmarks, it does not re-sort the file.
    rest = [str(p["id"]) for p in ordered[len(BENCHMARK_PORTFOLIOS) :]]
    assert rest == [str(p["id"]) for p in PORTFOLIOS if str(p["id"]) not in BENCHMARK_PORTFOLIOS]


async def test_the_seeded_benchmarks_are_ranked_and_hold_no_strategy(tmp_path: Path) -> None:
    """The yardstick contract, seed half (`BENCHMARK_PORTFOLIOS`, owner
    2026-08-15): a benchmark is ENABLED so the weekly ranking shows it, is never
    the defender, and holds no `Strategy` vertex via `holds` (no `HOLDS_EDGES`
    entry) — none of the three is a component of a strategy `replay.py` scores
    as a whole.

    `framework_id == "passive"` was true of BOTH benchmarks that existed through
    2026-08-15 and got folded into this test as if it were part of the
    contract — until `aaaf-r-USD` arrived (2026-08-18), an ACTIVELY managed
    momentum + min-variance rule that is a benchmark for an unrelated reason
    (its own adoption test was never run, db/seed_data.py "aaaf-r-USD"). "No
    active view" and "refused to propose" are independent properties; only the
    second is checked for every member below (CLAUDE.md "WHEN A SECOND ONE
    ARRIVES, FIND WHAT NAMED THE FIRST").

    The other half of the contract — never proposable — is behavioural and lives
    in `test_replay.py`, because what enforces it is the challenger search
    skipping the kind. It is NOT the concentration cap: `spy-USD` would fail
    that anyway, `all-weather-USD` (40% largest sleeve) would not, and relying
    on it was the accident this kind replaced."""
    settings = _test_settings(tmp_path)
    await run_seed(settings, fetch_raw=_stub_fetch_raw, yahoo_rate_limit_seconds=0.0)
    db = InvestmentDB(settings.db_path)
    try:
        rows = await db.query(
            "SELECT p.id, p.enabled, p.defender, p.framework_id, "
            "(SELECT COUNT(*) FROM holds WHERE holds.portfolio_id = p.id) AS strategies "
            "FROM portfolio p WHERE p.id IN "
            "(" + ", ".join(f"'{pid}'" for pid in sorted(BENCHMARK_PORTFOLIOS)) + ")"
        )
        assert len(rows) == len(BENCHMARK_PORTFOLIOS)
        passive = {"all-weather-USD", "spy-USD"}
        for row in rows:
            assert row["enabled"], row["id"]  # ranked every week
            assert not row["defender"], row["id"]  # never the thing being defended
            assert row["strategies"] == 0, row["id"]
            if row["id"] in passive:
                assert row["framework_id"] == "passive", row["id"]
            else:
                assert row["framework_id"] != "passive", row["id"]
    finally:
        await db.close()


async def test_seed_allocations_respect_binding_caps(tmp_path: Path) -> None:
    """Every seeded portfolio/scenario allocation sums to 100 and respects
    the binding single-asset cap (REVISION_NOTES 'Risk rules').

    EXCEPT the yardsticks (`BENCHMARK_PORTFOLIOS`), and the exception is the
    rule working rather than a hole in it: `spy-USD` is 100% one asset against a
    60% cap, which is exactly what keeps a benchmark ranked and never held. The
    sum-to-100 half still binds — an allocation that does not sum is a seed bug
    whatever the row is for."""
    import json

    settings = _test_settings(tmp_path)
    await run_seed(settings, fetch_raw=_stub_fetch_raw, yahoo_rate_limit_seconds=0.0)
    db = InvestmentDB(settings.db_path)
    try:
        for row in await db.query("SELECT id, allocation, max_single_asset_pct FROM portfolio"):
            allocation = json.loads(row["allocation"])
            assert abs(sum(allocation.values()) - 100) < 1e-9, row["id"]
            if str(row["id"]) in BENCHMARK_PORTFOLIOS:
                continue
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


async def test_the_event_log_is_append_only_in_the_database_not_by_convention(
    tmp_path: Path,
) -> None:
    """CLAUDE.md's "append-only" was an application habit — nothing stopped an
    UPDATE or a DELETE on the audit spine every verdict is reconstructed from,
    which is precisely the table where a silent edit leaves no trace of itself.
    """
    db = InvestmentDB(tmp_path / "append.db")
    try:
        await db.append_event(type="T", source_uc="UC0", source_id=None, payload={"n": 1})
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            await db.command("UPDATE event_log SET payload = '{\"n\": 2}'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            await db.command("DELETE FROM event_log")
        rows = await db.query("SELECT payload FROM event_log")
        assert [r["payload"] for r in rows] == ['{"n": 1}']  # untouched
    finally:
        await db.close()


async def _series(db: InvestmentDB, ticker: str, stamps: list[str]) -> None:
    await db.append_ts_batch(
        "market_data",
        [
            {"ticker": ticker, "asset_class": "MACRO", "currency": "USD", "ts": ts, "level": 1.0}
            for ts in stamps
        ],
    )


async def test_a_re_dated_series_replaces_itself_instead_of_doubling(tmp_path: Path) -> None:
    """The defect this method exists for, twice measured on `market_data`:
    `append_ts_batch` is keyed on `(ticker, ts)`, so it is idempotent only while
    the DATING holds. Re-date the same observations — a changed
    `availability_lag_days`, source or transform — and the new rows land BESIDE
    the old ones. In M5 that gave M2SL 1768 rows for 418 observations and made
    `m2_yoy` report -62.6%..+213.9% growth; on 2026-08-23 the M2SL lag
    correction doubled `m2_yoy` to 814 rows."""
    db = InvestmentDB(tmp_path / "redate.db")
    try:
        await _series(db, "M2SL", ["2026-05-18", "2026-06-18", "2026-07-18"])
        # the SAME observations, published-dated 43 days later
        shortfall = await db.replace_ts_series(
            "market_data",
            "M2SL",
            [
                {
                    "ticker": "M2SL",
                    "asset_class": "MACRO",
                    "currency": "USD",
                    "ts": ts,
                    "level": 1.0,
                }
                for ts in ("2026-06-30", "2026-07-31", "2026-08-31")
            ],
        )
        assert shortfall is None
        rows = await db.query("SELECT ts FROM market_data WHERE ticker = 'M2SL' ORDER BY ts")
        assert [r["ts"] for r in rows] == ["2026-06-30", "2026-07-31", "2026-08-31"]
    finally:
        await db.close()


async def test_a_truncated_source_never_wipes_the_history_it_cannot_cover(tmp_path: Path) -> None:
    """I-30's "never delete on a hunch". The weekly chain runs unattended, so a
    vendor returning a short window must degrade to an additive write and SAY
    so — not silently replace 35 years with three months."""
    db = InvestmentDB(tmp_path / "truncated.db")
    try:
        await _series(db, "SPY", ["1991-01-02", "2008-06-02", "2026-08-21"])
        shortfall = await db.replace_ts_series(
            "market_data",
            "SPY",
            [
                {"ticker": "SPY", "asset_class": "MACRO", "currency": "USD", "ts": ts, "level": 2.0}
                for ts in ("2026-08-20", "2026-08-21")
            ],
        )
        assert shortfall is not None and "delete abandoned" in shortfall
        rows = await db.query("SELECT ts FROM market_data WHERE ticker = 'SPY' ORDER BY ts")
        assert "1991-01-02" in [r["ts"] for r in rows]  # the 35 years survive
    finally:
        await db.close()


async def test_replace_refuses_a_table_that_is_not_a_time_series(tmp_path: Path) -> None:
    db = InvestmentDB(tmp_path / "wrongtable.db")
    try:
        with pytest.raises(ValueError, match="not a time-series table"):
            await db.replace_ts_series("invariant", "x", [{"ticker": "x", "ts": "2026-01-01"}])
    finally:
        await db.close()
