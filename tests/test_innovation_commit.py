"""new_invariant innovation commit through the SHARED dedup gate (docs/TASKS.md
Phase 6; src/investment/writeback/writeback.py commit_innovations). A stub
embedder stands in for the model (find_duplicate works on vectors); the
structural-identity dedup needs no cosine, so it is deterministic."""

import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from investment.corpus.embedding import to_blob
from investment.db.seed_data import INVARIANT_AUTHOR_CONFIG
from investment.db.sqlite import InvestmentDB
from investment.mechanical.invariants import REFERENCE_STATUS, mature_seed_invariants
from investment.planner.post import PostPlannerResult
from investment.worker.result import ImprovementProposal
from investment.writeback.writeback import commit_innovations

# the invariant-maturation thresholds mature_seed_invariants reads
_MATURATION_THRESHOLDS = {
    "proposal_outcome_weeks": 12.0,
    "recency_half_life_days": 365.0,
    "invariant_min_confrontations": 3.0,
    "invariant_time_validation_score": 0.6,
    "invariant_refuted_min_confrontations": 4.0,
    "invariant_refuted_score": 0.35,
    "invariant_verdict_confidence": 0.95,
    "invariant_null_score": 0.5,
    "confrontation_margin": 0.1,
    "confrontation_margin_return": 0.02,
}

_CONDITION = [{"signal": "real_yield", "feature": "level", "op": "<", "value": 0.0}]
_EFFECT = {
    "handle": "asset-class:gold-commodities",
    "metric": "return",
    "method": "cross_class",
    "direction": "outperform",
}


class _StubEmbedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)


def _innovation(title: str) -> ImprovementProposal:
    return ImprovementProposal(
        type="new_invariant",
        title=title,
        rationale="gold outperforms when real yields are negative",
        spec={"id": "inv-new", "condition": _CONDITION, "effect": _EFFECT, "tags": ["gold"]},
        weight_initial=0.5,
        floor_weight=0.2,
        trace="UC8 agent-discovery",
    )


async def _seed_thresholds(db: InvestmentDB) -> None:
    for key, value in _MATURATION_THRESHOLDS.items():
        await db.command(
            "INSERT INTO system_thresholds (key, value, updated_at) VALUES (:k, :v, '2026-01-01')",
            k=key,
            v=value,
        )


async def _seed_author_bands(db: InvestmentDB) -> None:
    """The SEEDED tier bands, imported rather than retyped: the assertions below
    pin exact numbers (0.05 floor, 0.15-0.25 initial on the 'system' tier that
    `ImprovementProposal.author` defaults to), so a local copy would keep
    testing the old bands, greenly, after a re-tune of the real ones."""
    for row in INVARIANT_AUTHOR_CONFIG:
        await db.command(
            "INSERT INTO invariant_author_config "
            "(author, floor_weight, initial_weight_min, initial_weight_max) "
            "VALUES (:author, :floor_weight, :initial_weight_min, :initial_weight_max)",
            **row,
        )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "inv.db")
    await _seed_thresholds(conn)
    await _seed_author_bands(conn)
    yield conn
    await conn.close()


async def test_new_invariant_is_created_proposed_and_agent_discovery(db: InvestmentDB) -> None:
    result = PostPlannerResult(
        innovations=[_innovation("Gold beats when real yields are negative")]
    )
    n = await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    assert n == 1
    row = (await db.query("SELECT source, status FROM invariant WHERE id='inv-new'"))[0]
    assert row["source"] == "agent-discovery"
    assert row["status"] == "proposed"  # earns its verdict from the 35y sweep (ADR-006)
    ev = await db.query("SELECT source_id FROM event_log WHERE type='InnovationEvent'")
    assert ev[0]["source_id"] == "inv-new"


async def test_a_structural_duplicate_is_merged_not_recreated(db: InvestmentDB) -> None:
    # an existing invariant with the SAME condition+effect and an embedding
    await db.command(
        "INSERT INTO invariant (id, title, description, source, status, condition, effect, "
        "weight_initial, floor_weight, weight_effective, embedding, trace, created_at, updated_at) "
        "VALUES ('inv-existing', 't', 'd', 'curator', 'integrated', :cond, :eff, 0.5, 0.2, 0.6, "
        ":emb, 'tr', '2026-01-01', '2026-01-01')",
        cond=json.dumps(_CONDITION),
        eff=json.dumps(_EFFECT),
        emb=to_blob(np.ones(4, dtype=np.float32)),
    )
    result = PostPlannerResult(innovations=[_innovation("Same claim, different words")])
    n = await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    assert n == 0  # merged into the incumbent, not recreated
    assert await db.query("SELECT id FROM invariant WHERE id='inv-new'") == []  # never created
    ev = await db.query(
        "SELECT json_extract(payload, '$.merged_into') AS m FROM event_log "
        "WHERE type='InnovationEvent'"
    )
    assert ev[0]["m"] == "inv-existing"


async def test_the_author_tier_band_binds_the_worker_proposed_weights(db: InvestmentDB) -> None:
    """CLAUDE.md "Invariant weight model": the floor is the TIER's, not the
    LLM's. `_innovation` proposes 0.5/0.2 on the 'system' tier, whose band is
    0.05 floor and 0.15-0.25 initial."""
    result = PostPlannerResult(innovations=[_innovation("Gold beats on negative real yields")])
    await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    row = (await db.query("SELECT weight_initial, floor_weight FROM invariant WHERE id='inv-new'"))[
        0
    ]
    assert row["floor_weight"] == 0.05  # the tier's, not the proposed 0.2
    assert row["weight_initial"] == 0.25  # 0.5 clamped down to the band's max


async def test_a_zero_weight_proposal_is_lifted_to_the_band_not_born_inert(
    db: InvestmentDB,
) -> None:
    """`ImprovementProposal` defaults both weights to 0.0 (worker/result.py), and
    `weight_effective = max(0 x score x recency, 0)` is zero forever — an
    invariant that could never influence anything however often confirmed."""
    innovation = _innovation("Gold beats on negative real yields")
    result = PostPlannerResult(
        innovations=[innovation.model_copy(update={"weight_initial": 0.0, "floor_weight": 0.0})]
    )
    await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    row = (
        await db.query(
            "SELECT weight_initial, floor_weight, weight_effective "
            "FROM invariant WHERE id='inv-new'"
        )
    )[0]
    assert row["weight_initial"] == 0.15  # lifted to the band's min
    assert row["floor_weight"] == 0.05
    assert float(row["weight_effective"]) > 0.0


async def test_two_near_identical_innovations_in_ONE_batch_dedup_against_each_other(
    db: InvestmentDB,
) -> None:
    """The corpus is read once, before the loop: without growing it in flight,
    the second member of a batch deduplicates against a snapshot that predates
    the first and both are created."""
    first = _innovation("Gold beats when real yields are negative")
    second = _innovation("Negative real yields favour gold")
    # distinct ids, so a failure creates TWO rows rather than colliding on the PK
    result = PostPlannerResult(
        innovations=[
            first,
            second.model_copy(update={"spec": {**second.spec, "id": "inv-new-2"}}),
        ]
    )
    n = await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    assert n == 1  # the second merged into the first
    assert len(await db.query("SELECT id FROM invariant")) == 1
    merged = await db.query(
        "SELECT json_extract(payload, '$.merged_into') AS m FROM event_log "
        "WHERE type='InnovationEvent' AND json_extract(payload, '$.merged_into') IS NOT NULL"
    )
    assert [r["m"] for r in merged] == ["inv-new"]


async def test_reference_knowledge_gets_its_own_terminal_status(db: InvestmentDB) -> None:
    """209 of the live corpus's 238 'proposed' invariants were reference notes —
    no condition, no effect, so no confrontation can ever move them. Filing them
    as 'proposed' made ADR-006's "nothing stays proposed forever" an open promise
    the system could not keep for 88% of the rows carrying that status."""
    await db.command(
        "INSERT INTO invariant (id, title, description, source, status, condition, effect, "
        "weight_initial, floor_weight, weight_effective, trace, created_at, updated_at) "
        "VALUES ('inv-ref', 'a fact', 'd', 'curator', 'proposed', '[]', NULL, 0.4, 0.2, "
        "0.4, 'tr', '2026-01-01', '2026-01-01')"
    )
    await mature_seed_invariants(db)
    row = (await db.query("SELECT status FROM invariant WHERE id = 'inv-ref'"))[0]
    assert row["status"] == REFERENCE_STATUS


async def test_the_backfill_is_the_maturation_sweep_itself(db: InvestmentDB) -> None:
    """No migration script: the reference branch runs BEFORE the
    already-matured fingerprint check, so every existing note is re-stamped on
    the next sweep. The 209 live rows convert themselves."""
    for n in range(3):
        await db.command(
            "INSERT INTO invariant (id, title, description, source, status, condition, effect, "
            "weight_initial, floor_weight, weight_effective, trace, created_at, updated_at) "
            "VALUES (:id, 'a fact', 'd', 'curator', 'proposed', '[]', NULL, 0.4, 0.2, 0.4, "
            "'tr', '2026-01-01', '2026-01-01')",
            id=f"inv-old-{n}",
        )
    await mature_seed_invariants(db)
    await mature_seed_invariants(db)  # idempotent
    rows = await db.query("SELECT status FROM invariant WHERE id LIKE 'inv-old-%'")
    assert [r["status"] for r in rows] == [REFERENCE_STATUS] * 3
