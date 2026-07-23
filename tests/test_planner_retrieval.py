"""Planner variable-margin retrieval (docs/TASKS.md Task 4.1;
src/investment/planner/retrieval.py). Pure cosine/top-k logic directly; the
two-matrix retrieval and the whitelisted zooms against a real throwaway SQLite
with synthetic dim-4 embeddings (no model load — the cosine math is identical at
any dimension)."""

from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest

from investment.corpus.embedding import to_blob
from investment.db.sqlite import InvestmentDB
from investment.planner import retrieval as R


def _vec(*xs: float) -> np.ndarray:
    return np.array(xs, dtype=np.float32)


# -- pure core ---------------------------------------------------------------


def test_top_k_union_keys_each_index_to_its_best_similarity() -> None:
    sims = np.array([[0.9, 0.1, 0.5], [0.2, 0.8, 0.4]])  # 2 queries x 3 corpus
    # q0 top-2 = {0:0.9, 2:0.5}; q1 top-2 = {1:0.8, 2:0.4}. Union keeps 2's BEST
    # (0.5), sorted by similarity descending.
    assert R.top_k_union(sims, k=2) == [(0, 0.9), (1, 0.8), (2, 0.5)]


def test_top_k_union_empty_is_empty() -> None:
    assert R.top_k_union(np.empty((0, 0)), k=8) == []


def test_query_strategies_truncates_to_the_contract_caps() -> None:
    qs = R.QueryStrategies(
        corpus_queries=["a", "b", "c", "d"],
        zooms=[R.Zoom(kind=R.ZoomKind.regime_history, arg="1")] * 5,
    )
    assert len(qs.corpus_queries) == R.MAX_QUERIES == 3
    assert len(qs.zooms) == R.MAX_ZOOMS == 3


def test_safe_window_parses_caps_and_defaults() -> None:
    assert R._safe_window("6", default=12, cap=20) == 6
    assert R._safe_window("999", default=12, cap=20) == 20  # capped
    assert R._safe_window("junk", default=12, cap=20) == 12  # falls back


# -- integration: two-matrix retrieval ---------------------------------------


async def _seed_corpus(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    await cmd(
        "INSERT INTO document (id, title, kind, source_type, ingested_at, trace) "
        "VALUES ('d1', 'D', 'book', 'text', '2026-01-01', 't')"
    )
    # a passage matching the query, and an off-topic one
    for pid, content, emb in (
        ("p1", "gold in stagflation", _vec(1, 0, 0, 0)),
        ("p2", "unrelated", _vec(0, 0, 0, 1)),
    ):
        await cmd(
            "INSERT INTO passage (id, document_id, position, content, embedding, created_at) "
            "VALUES (:id, 'd1', 0, :c, :e, '2026-01-01')",
            id=pid,
            c=content,
            e=to_blob(emb),
        )
    # i-direct matches the query and has NO supporting passage (reachable ONLY
    # via the direct invariant matrix); i-linked is orthogonal to the query and
    # reachable ONLY via the SUPPORTS edge from p1
    for iid, emb in (("i-direct", _vec(1, 0, 0, 0)), ("i-linked", _vec(0, 1, 0, 0))):
        await cmd(
            "INSERT INTO invariant (id, title, description, source, status, tags, "
            "weight_initial, floor_weight, weight_effective, embedding, trace, created_at, "
            "updated_at) VALUES (:id, 't', 'd', 's', 'integrated', '[]', 0.5, 0.2, 0.7, :e, "
            "'tr', '2026-01-01', '2026-01-01')",
            id=iid,
            e=to_blob(emb),
        )
    await cmd(
        "INSERT INTO supports (passage_id, invariant_id, strength, excerpt) "
        "VALUES ('p1', 'i-linked', 0.9, 'x')"
    )


@pytest.fixture
async def corpus(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    db = InvestmentDB(tmp_path / "r.db")
    await _seed_corpus(db)
    yield db
    await db.close()


async def test_retrieve_reaches_both_the_supports_and_direct_paths(corpus: InvestmentDB) -> None:
    query = _vec(1, 0, 0, 0).reshape(1, 4)
    # invariant_k=1: the direct matrix returns ONLY i-direct, so i-linked can
    # appear only through the SUPPORTS edge — isolating the two paths.
    pool = await R.retrieve(corpus, query, zooms=[], passage_k=1, invariant_k=1)

    assert [p["id"] for p in pool.passages] == ["p1"]
    assert pool.passages[0]["similarity"] == pytest.approx(1.0)
    ids = {i["id"] for i in pool.invariants}
    assert "i-linked" in ids  # SUPPORTS path (excluded from the k=1 direct hit)
    assert "i-direct" in ids  # direct-matrix path (has no supporting passage)


async def test_no_queries_skips_cosine_but_still_runs_zooms(corpus: InvestmentDB) -> None:
    pool = await R.retrieve(
        corpus, np.empty((0, 4)), zooms=[R.Zoom(kind=R.ZoomKind.regime_history, arg="5")]
    )
    assert pool.passages == []
    assert pool.invariants == []  # no passages retrieved -> no SUPPORTS ids either
    assert pool.zoom_results[0]["kind"] == "regime_history"


# -- integration: the whitelisted zooms --------------------------------------


async def _seed_zoom_targets(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO regime_type (id, name, aliases, framework_id, description, created_at) "
        "VALUES ('stag', 'Stag', '[]', '4s', 'd', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
        "conditions, source, status, trace, created_at, updated_at) VALUES ('s1', 't', 'd', "
        "'4s', 60, 1, 'c', 'corpus', 'active', 'tr', '2026-01-01', '2026-01-01')"
    )
    for i in range(25):  # more than ZOOM_ROW_CAP, to prove the bound
        await cmd(
            "INSERT INTO regime (id, regime_type_id, tags, start_date, is_current, events, "
            "trace, created_at, updated_at) VALUES (:id, 'stag', '[]', :d, 0, '[]', 't', "
            "'2026-01-01', '2026-01-01')",
            id=f"reg-{i:02d}",
            d=f"2020-{(i % 12) + 1:02d}-01",
        )
    await cmd(
        "INSERT INTO favors (regime_type_id, strategy_id, sortino_rolling, sharpe_rolling, "
        "calmar_rolling, max_drawdown, n_periods, last_updated) VALUES ('stag', 's1', 1.2, 0.9, "
        "1.1, -0.1, 40, '2026-01-01')"
    )
    await cmd(
        "INSERT INTO invariant (id, title, description, source, status, tags, weight_initial, "
        "floor_weight, weight_effective, trace, created_at, updated_at) VALUES ('inv1', 't', 'd', "
        "'s', 'integrated', '[]', 0.5, 0.2, 0.7, 'tr', '2026-01-01', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO invariant_confrontations (id, invariant_id, moment_context, date, verdict, "
        "severity, source, source_id) VALUES ('c1', 'inv1', 'ctx', '2026-05-01', 'confirmed', 1.0, "
        "'backtest', NULL)"
    )
    await cmd(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, recommendation, "
        "market_context, reasoning, trace, created_at) VALUES ('prop1', '2026-05-01', 'switch', "
        "'def', 'monitor', '{}', 'r', 't', '2026-05-01')"
    )


@pytest.fixture
async def zoomdb(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    db = InvestmentDB(tmp_path / "z.db")
    await _seed_zoom_targets(db)
    yield db
    await db.close()


async def test_zoom_strategy_history(zoomdb: InvestmentDB) -> None:
    out = await R.execute_zoom(zoomdb, R.Zoom(kind=R.ZoomKind.strategy_history, arg="s1"))
    assert out["kind"] == "strategy_history"
    assert out["rows"][0]["sortino_rolling"] == pytest.approx(1.2)


async def test_zoom_invariant_confrontations(zoomdb: InvestmentDB) -> None:
    out = await R.execute_zoom(
        zoomdb, R.Zoom(kind=R.ZoomKind.invariant_confrontations, arg="inv1")
    )
    assert out["rows"][0]["verdict"] == "confirmed"


async def test_zoom_regime_history_is_bounded(zoomdb: InvestmentDB) -> None:
    # arg asks for 999; the cap holds it at ZOOM_ROW_CAP even with 25 rows seeded
    out = await R.execute_zoom(zoomdb, R.Zoom(kind=R.ZoomKind.regime_history, arg="999"))
    assert len(out["rows"]) == R.ZOOM_ROW_CAP


async def test_zoom_proposal_thread(zoomdb: InvestmentDB) -> None:
    out = await R.execute_zoom(zoomdb, R.Zoom(kind=R.ZoomKind.proposal_thread, arg="prop1"))
    assert [r["id"] for r in out["rows"]] == ["prop1"]
