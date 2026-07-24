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
from investment.db.sqlite import InvestmentDB
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


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "inv.db")
    await _seed_thresholds(conn)
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
