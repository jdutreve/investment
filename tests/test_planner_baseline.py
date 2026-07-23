"""Planner mechanical baseline (docs/TASKS.md Task 4.1 steps 1-5;
src/investment/planner/baseline.py). Pure helpers are tested directly; the
5-query assembly runs against a real throwaway SQLite seeded with the minimal
FK chain (CLAUDE.md: real DB, no mocks — one integration test per mechanical
job)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.planner import baseline as bl

# -- pure helpers (no DB) ----------------------------------------------------


def test_held_assets_unions_every_ranked_allocation() -> None:
    ranking = [
        {"allocation": {"SPY": 60, "cash": 40}},
        {"allocation": {"GLD": 50, "SPY": 50}},
        {"allocation": "not-parsed"},  # a row whose JSON failed to parse is skipped
    ]
    assert bl.held_assets(ranking) == {"SPY", "cash", "GLD"}


def test_dedupe_keeps_first_bucket_slot_and_caps() -> None:
    regime = [{"id": "a"}, {"id": "b"}]
    assets = [{"id": "b"}, {"id": "c"}]  # b overlaps regime → kept in regime's slot
    glob = [{"id": "c"}, {"id": "d"}, {"id": "e"}]
    merged = bl.dedupe_buckets(regime, assets, glob, cap=4)
    assert [i["id"] for i in merged] == ["a", "b", "c", "d"]  # e dropped by the cap


def test_empty_assets_predicate_is_never_true() -> None:
    where, params = bl._asset_tag_predicate(set())
    assert where == "0" and params == {}


def test_asset_predicate_matches_the_json_tag_shape() -> None:
    where, params = bl._asset_tag_predicate({"GLD", "SPY"})
    assert where == "(tags LIKE :a0 OR tags LIKE :a1)"
    assert set(params.values()) == {'%"asset:GLD"%', '%"asset:SPY"%'}


# -- integration: the 5 queries against a seeded throwaway DB ----------------


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4seasons', 'Four Seasons', 1, 't', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO regime_type (id, name, aliases, framework_id, description, created_at) "
        "VALUES ('stagflation', 'Stagflation', '[\"falling-growth-rising-inflation\"]', "
        "'4seasons', 'd', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO regime (id, regime_type_id, tags, start_date, is_current, events, "
        "trace, created_at, updated_at) VALUES ('stag-2026', 'stagflation', '[]', "
        "'2026-06-01', 1, '[\"CPI up\"]', 't', '2026-06-01', '2026-06-01')"
    )
    # two GLOBAL_LIQUIDITY rows — the baseline must read the LATEST
    for ts, level in (("2026-06-01", 90.0), ("2026-06-08", 95.0)):
        await cmd(
            "INSERT INTO market_data (ticker, asset_class, currency, ts, level, speed) "
            "VALUES ('GLOBAL_LIQUIDITY', 'MACRO', 'USD', :ts, :lvl, 0.5)",
            ts=ts,
            lvl=level,
        )
    # ranking: two dates; only the latest is read, rank ASC
    for date, pid, rank, alloc in (
        ("2026-06-01", "old", 1, '{"SPY": 100}'),  # stale date — must be ignored
        ("2026-06-08", "defender", 2, '{"GLD": 60, "cash": 40}'),
        ("2026-06-08", "challenger", 1, '{"TLT": 100}'),
    ):
        await cmd(
            "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
            "allocation, rank, market_context, recommendation, trace) "
            "VALUES (:d, :p, 0, '4seasons', :a, :r, '{}', 'maintain', 't')",
            d=date,
            p=pid,
            a=alloc,
            r=rank,
        )
    # scenarios: two ts for one (strategy, scenario) → week-over-week shift
    for ts, prob in (("2026-06-01", 50.0), ("2026-06-08", 62.0)):
        await cmd(
            "INSERT INTO scenario_probability (strategy_id, scenario, ts, probability) "
            "VALUES ('s1', 'bull', :ts, :p)",
            ts=ts,
            p=prob,
        )
    # invariants: statuses + tags spanning all three buckets, plus a proposed one
    # that must NEVER surface (integrated-only)
    invs = [
        ("i-regime", "integrated", '["regime:stagflation"]', 0.5),
        ("i-asset", "integrated", '["asset:GLD"]', 0.9),  # heaviest, but not regime-tagged
        ("i-both", "integrated", '["regime:stagflation", "asset:TLT"]', 0.7),
        ("i-global", "integrated", '["misc"]', 0.8),
        ("i-proposed", "proposed", '["regime:stagflation", "asset:GLD"]', 0.99),
    ]
    for iid, status, tags, weff in invs:
        await cmd(
            "INSERT INTO invariant (id, title, description, source, status, tags, "
            "weight_initial, floor_weight, weight_effective, trace, created_at, updated_at) "
            "VALUES (:id, 't', 'd', 's', :st, :tg, 0.5, 0.2, :w, 'tr', '2026-01-01', '2026-01-01')",
            id=iid,
            st=status,
            tg=tags,
            w=weff,
        )
    await cmd(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, recommendation, "
        "market_context, reasoning, trace, created_at) VALUES ('p1', '2026-06-08', 'switch', "
        "'defender', 'monitor', '{}', 'r', 't', '2026-06-08')"
    )


@pytest.fixture
async def seeded(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    db = InvestmentDB(tmp_path / "b.db")
    await _seed(db)
    yield db
    await db.close()


async def test_gather_baseline_shapes(seeded: InvestmentDB) -> None:
    b = await bl.gather_baseline(seeded)
    assert b.regime["regime_type_id"] == "stagflation"
    assert b.regime["regime_name"] == "Stagflation"
    assert b.regime["events"] == ["CPI up"]  # JSON parsed
    assert b.global_liquidity["level"] == 95.0  # latest row, not the stale one
    # ranking is the LATEST date only, rank ASC
    assert [r["portfolio_id"] for r in b.ranking] == ["challenger", "defender"]
    assert b.ranking[0]["allocation"] == {"TLT": 100}  # JSON parsed
    assert len(b.recent_proposals) == 1


async def test_scenario_shift_is_week_over_week(seeded: InvestmentDB) -> None:
    b = await bl.gather_baseline(seeded)
    (row,) = b.scenarios
    assert row["probability"] == 62.0
    assert row["shift"] == pytest.approx(12.0)  # 62 - 50


async def test_bucket_priority_dedup_and_integrated_only(seeded: InvestmentDB) -> None:
    b = await bl.gather_baseline(seeded)
    ids = [i["id"] for i in b.top_invariants]
    # regime bucket first (i-regime, i-both by weight), then held-asset bucket
    # (GLD/TLT/cash held → i-asset; i-both already seen), then global (i-global).
    # i-proposed never appears (integrated-only).
    assert ids == ["i-both", "i-regime", "i-asset", "i-global"]
    assert "i-proposed" not in ids


async def test_empty_db_degrades_without_crashing(tmp_path: Path) -> None:
    db = InvestmentDB(tmp_path / "empty.db")
    try:
        b = await bl.gather_baseline(db)
        assert b.regime == {}
        assert b.global_liquidity == {}
        assert b.ranking == []
        assert b.top_invariants == []
    finally:
        await db.close()
