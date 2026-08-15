"""The as-of-t snapshot the agentic replay reads (M8b — docs/TASKS.md Task 9.4;
src/investment/db/as_of_snapshot.py).

Two properties, and they pull in opposite directions on purpose: NOTHING from
after t survives in the world tables, and the CORPUS survives whole. A test that
only checked the first would pass on an empty file.

Real throwaway SQLite, no mocks (CLAUDE.md "Tests") — the module's whole job is
what SQLite does to a real database file.
"""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest

from investment.db.as_of_snapshot import (
    advance_as_of_snapshot,
    build_as_of_snapshot,
    snapshot_path,
)
from investment.db.sqlite import InvestmentDB

AS_OF = date(2008, 10, 1)


async def _seed(db: InvestmentDB) -> None:
    """Rows straddling AS_OF in every table the prune touches, plus a corpus."""

    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('fw', 'F', 1, 'tr', '1990-01-01')"
    )
    await cmd(
        "INSERT INTO regime_type (id, name, framework_id, description, created_at) "
        "VALUES ('stag', 'Stagflation', 'fw', 'd', '1990-01-01')"
    )

    # Two prices, one each side of t.
    for ts, level in (("2008-09-30", 100.0), ("2008-10-02", 200.0)):
        await cmd(
            "INSERT INTO market_data (ticker, asset_class, currency, ts, level) "
            "VALUES ('SPY', 'EQUITY', 'USD', :ts, :lv)",
            ts=ts,
            lv=level,
        )

    # Four regimes straddling t. `updated_at` is the CLOSING print — the date
    # the closure became knowable — which for a closed regime is always later
    # than its retroactive `end_date` (`market.regime._commit_regime` writes
    # both in one transaction). `is_current` is seeded WRONG on purpose, on the
    # 2026 regime, as the live DB has it.
    regimes = (
        # closed before t, and known to be: it stays closed
        ("r-old", "2007-01-01", "2007-12-31", "2007-02-01", "2008-03-31", 0),
        # retroactively ended before t, but the confirming prints land AFTER it:
        # at t nobody knew it had ended (I-49's knowability half)
        ("r-lagging", "2008-01-15", "2008-08-01", "2008-02-15", "2008-11-30", 0),
        # ends well after t — the older `end_date > t` case
        ("r-open", "2008-07-16", "2009-06-30", "2008-08-15", "2009-09-30", 0),
        # confirmed after t: invisible entirely
        ("r-future", "2009-01-01", None, "2009-02-01", "2009-02-01", 1),
    )
    for rid, start, end, created, updated, current in regimes:
        await cmd(
            "INSERT INTO regime (id, regime_type_id, start_date, end_date, is_current, "
            "trace, created_at, updated_at) VALUES (:id, 'stag', :s, :e, :c, 'tr', :ca, :ua)",
            id=rid,
            s=start,
            e=end,
            c=current,
            ca=created,
            ua=updated,
        )

    # A portfolio carrying 2026 indicators, and a FAVORS edge aggregating 35y —
    # neither row has a date of its own, so only a repair can reach them.
    await cmd(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, "
        "benchmark, allocation, max_drawdown_rule, max_single_asset_pct, phase, "
        "sortino_rolling, return_1y, trace, updated_at) VALUES ('pf', 'P', 'fw', 1, 1, 'USD', "
        "'SPY', '{}', -15, 40, 'accumulation', 1.849, 0.31, 'tr', '2026-07-15')"
    )

    # A strategy + backtests hanging off the visible and the invisible regime.
    await cmd(
        "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
        "conditions, source, status, trace, created_at, updated_at) VALUES ('st', 'S', 'd', "
        "'fw', 5, 1, '[]', 'corpus', 'active', 'tr', '1990-01-01', '1990-01-01')"
    )
    for bid, rid in (("bt-visible", "r-open"), ("bt-future", "r-future")):
        await cmd(
            "INSERT INTO backtest (id, strategy_id, regime_id, period, date_start, date_end, "
            "currency, trace, created_at) "
            # created_at is the SEED's wall clock on BOTH rows — the prune must
            # ignore it and follow the regime instead.
            "VALUES (:id, 'st', :r, 'regime', '2008-07-16', '2009-06-30', 'USD', 'tr', "
            "'2026-07-15T11:13:15+00:00')",
            id=bid,
            r=rid,
        )

    await cmd(
        "INSERT INTO favors (regime_type_id, strategy_id, sortino_rolling, n_periods, "
        "last_updated) VALUES ('stag', 'st', 1.42, 9, '2026-07-15')"
    )

    # The corpus: an invariant born long after t. It must SURVIVE (module
    # docstring — pruning it empties gate 6 and the screen measures nothing).
    await cmd(
        "INSERT INTO invariant (id, title, description, source, status, condition, "
        "weight_initial, floor_weight, trace, created_at, updated_at) "
        "VALUES ('inv-2026', 't', 'd', 's', 'integrated', '[]', 0.5, 0.2, 'tr', "
        "'2026-07-15', '2026-07-15')"
    )

    # The agent's own history. BOTH rows were appended in July 2026 (`ts`), but
    # the regime one is DATED 2008 — the 35y backfill's shape. Pruning on `ts`
    # would drop both; only `event_date` keeps what was knowable at t.
    events = (
        ("01J-regime", "2008-08-15", "RegimeEvent"),
        ("01J-ranking", "2026-07-14", "RankingEvent"),
    )
    for eid, event_date, etype in events:
        await cmd(
            "INSERT INTO event_log (id, ts, event_date, type, source_uc, payload) "
            "VALUES (:id, '2026-07-12T17:15:17+00:00', :d, :ty, 'UC7', '{}')",
            id=eid,
            d=event_date,
            ty=etype,
        )


@pytest.fixture
async def live(tmp_path: Path) -> AsyncIterator[Path]:
    path = tmp_path / "live.db"
    db = InvestmentDB(path)
    await _seed(db)
    await db.close()
    yield path


async def _snap(live: Path, tmp_path: Path) -> InvestmentDB:
    build_as_of_snapshot(live, snapshot_path(tmp_path, AS_OF), AS_OF)
    return InvestmentDB(snapshot_path(tmp_path, AS_OF))


async def test_world_rows_after_t_are_gone(live: Path, tmp_path: Path) -> None:
    db = await _snap(live, tmp_path)
    try:
        prices = await db.query("SELECT ts FROM market_data ORDER BY ts")
        assert [r["ts"] for r in prices] == ["2008-09-30"]

        regimes = await db.query("SELECT id FROM regime ORDER BY id")
        assert [r["id"] for r in regimes] == ["r-lagging", "r-old", "r-open"]  # r-future invisible

        # Pruned on the DOMAIN date: the 2008-dated regime event was knowable at
        # t even though it was appended in 2026 by the backfill.
        events = await db.query("SELECT id FROM event_log ORDER BY id")
        assert [r["id"] for r in events] == ["01J-regime"]
    finally:
        await db.close()


async def test_backtests_follow_their_regime_not_their_seed_timestamp(
    live: Path, tmp_path: Path
) -> None:
    """Both backtests carry the same 2026 `created_at`. Pruning on it would drop
    both and leave the as-of FAVORS aggregation with nothing to read."""
    db = await _snap(live, tmp_path)
    try:
        rows = await db.query("SELECT id FROM backtest ORDER BY id")
        assert [r["id"] for r in rows] == ["bt-visible"]
    finally:
        await db.close()


async def test_open_regime_looks_open_and_is_current_is_recomputed(
    live: Path, tmp_path: Path
) -> None:
    db = await _snap(live, tmp_path)
    try:
        rows = await db.query("SELECT id, end_date, is_current FROM regime ORDER BY id")
        by_id = {str(r["id"]): r for r in rows}
        # Closed BEFORE t and KNOWN to be: it stays closed.
        assert by_id["r-old"]["end_date"] == "2007-12-31"
        # Ended before t on paper, but its confirming prints landed after t — so
        # at t it still looked open. This is the half the `end_date` rule missed
        # and the reason the repair keys on `updated_at` (I-49).
        assert by_id["r-lagging"]["end_date"] is None
        # Ends after t: nobody knew this regime would end at all.
        assert by_id["r-open"]["end_date"] is None
        assert by_id["r-old"]["is_current"] == 0
        assert by_id["r-open"]["is_current"] == 1  # latest VISIBLE, not 2026's
    finally:
        await db.close()


async def test_the_corpus_survives_whole(live: Path, tmp_path: Path) -> None:
    """The deliberate asymmetry: an invariant born in 2026 is still citable at a
    2008 decision date, because M8b is a BEST-CASE screen (module docstring)."""
    db = await _snap(live, tmp_path)
    try:
        rows = await db.query("SELECT id, status FROM invariant")
        assert [(r["id"], r["status"]) for r in rows] == [("inv-2026", "integrated")]
        assert await db.query("SELECT id FROM strategy") != []
    finally:
        await db.close()


async def test_the_append_only_guard_is_put_back(live: Path, tmp_path: Path) -> None:
    """Pruning the event log needs the append-only triggers lifted. They must
    come back, or the replay could rewrite the log it appends to."""
    db = await _snap(live, tmp_path)
    try:
        with pytest.raises(Exception, match="append-only"):
            await db.command("DELETE FROM event_log WHERE id = '01J-regime'")
        assert len(await db.query("SELECT id FROM event_log")) == 1
    finally:
        await db.close()


async def test_refuses_to_overwrite_an_existing_snapshot(live: Path, tmp_path: Path) -> None:
    dest = snapshot_path(tmp_path, AS_OF)
    build_as_of_snapshot(live, dest, AS_OF)
    with pytest.raises(FileExistsError):
        build_as_of_snapshot(live, dest, AS_OF)


async def test_the_live_database_is_untouched(live: Path, tmp_path: Path) -> None:
    """The snapshot is a COPY. A prune that reached the live file would delete
    the agent's history the first time M8b ran."""
    build_as_of_snapshot(live, snapshot_path(tmp_path, AS_OF), AS_OF)
    db = InvestmentDB(live)
    try:
        assert len(await db.query("SELECT ts FROM market_data")) == 2
        assert len(await db.query("SELECT id FROM regime")) == 4
        assert len(await db.query("SELECT id FROM event_log")) == 2
    finally:
        await db.close()


async def test_a_failed_prune_leaves_no_half_built_snapshot(
    live: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prune is one transaction. If a statement fails partway, the copy must
    still hold every row AND its append-only guard — a snapshot missing both
    would read as a legitimate, very early as-of date."""
    import investment.db.as_of_snapshot as M

    broken = (*M._PRUNE, ("market_data", "no_such_column > :t"))
    monkeypatch.setattr(M, "_PRUNE", broken)

    dest = snapshot_path(tmp_path, AS_OF)
    with pytest.raises(Exception, match="no_such_column"):
        build_as_of_snapshot(live, dest, AS_OF)

    db = InvestmentDB(dest)
    try:
        assert len(await db.query("SELECT ts FROM market_data")) == 2  # nothing deleted
        with pytest.raises(Exception, match="append-only"):
            await db.command("DELETE FROM event_log WHERE id = '01J-regime'")
    finally:
        await db.close()


async def test_derived_aggregates_with_no_date_are_cleared(live: Path, tmp_path: Path) -> None:
    """The two future-written artefacts that carry no timestamp of their own:
    the Portfolio vertex's indicators and the FAVORS edges. Both are rebuilt by
    hydrating the snapshot; leaving them would hand a 2008 Worker 2026 numbers
    through nothing more exotic than `SELECT * FROM portfolio`."""
    db = await _snap(live, tmp_path)
    try:
        rows = await db.query("SELECT sortino_rolling, return_1y FROM portfolio")
        assert rows and all(r["sortino_rolling"] is None and r["return_1y"] is None for r in rows)
        assert await db.query("SELECT strategy_id FROM favors") == []
    finally:
        await db.close()


# -- advancing the clock -----------------------------------------------------

LATER = date(2009, 7, 1)


async def _advanced(live: Path, tmp_path: Path) -> InvestmentDB:
    """One snapshot built at AS_OF, then advanced to LATER — the shape a replay
    sequence uses, as opposed to two independent builds."""
    dest = snapshot_path(tmp_path, AS_OF)
    build_as_of_snapshot(live, dest, AS_OF)
    advance_as_of_snapshot(dest, live, AS_OF, LATER)
    return InvestmentDB(dest)


async def test_advancing_tops_up_the_world(live: Path, tmp_path: Path) -> None:
    db = await _advanced(live, tmp_path)
    try:
        prices = await db.query("SELECT ts FROM market_data ORDER BY ts")
        assert [r["ts"] for r in prices] == ["2008-09-30", "2008-10-02"]

        regimes = await db.query("SELECT id FROM regime ORDER BY id")
        assert [r["id"] for r in regimes] == ["r-future", "r-lagging", "r-old", "r-open"]
        # and the backtest that hangs off the newly visible regime comes with it
        rows = await db.query("SELECT id FROM backtest ORDER BY id")
        assert [r["id"] for r in rows] == ["bt-future", "bt-visible"]
    finally:
        await db.close()


async def test_advancing_re_dates_the_regime_as_of_the_new_t(live: Path, tmp_path: Path) -> None:
    """`end_date` was blanked at build time. A closure that became knowable
    inside the interval must come BACK — reading the snapshot's own NULL cannot
    tell 'still open' from 'closed, and we hid it'.

    `r-lagging` is the row that makes this precise: it ended 2008-08-01, before
    the first t, but its confirming prints landed 2008-11-30 — inside the
    interval. Under the old `end_date` rule it was never hidden in the first
    place; under the knowability rule it is hidden at t and restored here."""
    db = await _advanced(live, tmp_path)
    try:
        rows = await db.query("SELECT id, end_date, is_current FROM regime ORDER BY id")
        by_id = {str(r["id"]): r for r in rows}
        assert by_id["r-lagging"]["end_date"] == "2008-08-01"  # knowable by LATER
        # Ends 2009-06-30 but is only CONFIRMED closed on 2009-09-30, past LATER:
        # still open as far as this snapshot may know.
        assert by_id["r-open"]["end_date"] is None
        assert by_id["r-future"]["end_date"] is None  # genuinely open
        assert by_id["r-future"]["is_current"] == 1  # confirmed inside the interval
        assert by_id["r-open"]["is_current"] == 0
    finally:
        await db.close()


async def test_advancing_leaves_the_agents_own_history_alone(live: Path, tmp_path: Path) -> None:
    """The whole point of advancing rather than rebuilding. A proposal the
    replay emitted at the earlier date must still be there afterwards, or every
    month replays as an opening entry."""
    dest = snapshot_path(tmp_path, AS_OF)
    build_as_of_snapshot(live, dest, AS_OF)

    db = InvestmentDB(dest)
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, proposed_allocation, "
        "recommendation, market_context, reasoning, trace, created_at) VALUES "
        "('p1', '2008-10-01', 'market-signal', 'pf', '{\"IEF\": 60}', 'paper-test', '{}', 'r', "
        "'tr', '2008-10-01')"
    )
    await db.close()

    advance_as_of_snapshot(dest, live, AS_OF, LATER)
    db = InvestmentDB(dest)
    try:
        held = await db.query("SELECT proposed_allocation FROM proposal WHERE id = 'p1'")
        assert held and str(held[0]["proposed_allocation"]) == '{"IEF": 60}'
    finally:
        await db.close()


async def test_the_clock_only_moves_forward(live: Path, tmp_path: Path) -> None:
    dest = snapshot_path(tmp_path, AS_OF)
    build_as_of_snapshot(live, dest, AS_OF)
    with pytest.raises(ValueError, match="forward only"):
        advance_as_of_snapshot(dest, live, LATER, AS_OF)
