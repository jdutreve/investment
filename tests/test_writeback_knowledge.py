"""M7 tests for `writeback/knowledge.py` — the three properties the
module exists for: idempotence, resumability, deduplication.

Real throwaway SQLite and the real embedder (CLAUDE.md "no mocks"): dedup is a
claim about embedding geometry, and a stubbed embedder would test the plumbing
while leaving the actual question — do two paraphrases land above 0.80? —
unmeasured.
"""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from investment.corpus.embedding import InProcessEmbedder
from investment.db.seed_data import INVARIANT_AUTHOR_CONFIG
from investment.db.sqlite import InvestmentDB
from investment.worker.curator import (
    CandidateScores,
    Effect,
    InvariantCandidate,
    Predicate,
    ReferenceNote,
    ScoredCandidate,
    curation_fingerprint,
)
from investment.writeback.knowledge import KnowledgeWriteback, author_tier

FINGERPRINT = curation_fingerprint("test-model", "high")


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    database = InvestmentDB(tmp_path / "test.db")
    for row in INVARIANT_AUTHOR_CONFIG:
        await database.command(
            "INSERT INTO invariant_author_config "
            "(author, floor_weight, initial_weight_min, initial_weight_max) "
            "VALUES (:author, :floor_weight, :initial_weight_min, :initial_weight_max)",
            **row,
        )
    yield database
    await database.close()


@pytest.fixture(scope="module")
def embedder() -> InProcessEmbedder:
    return InProcessEmbedder("all-MiniLM-L6-v2")


async def _document(db: InvestmentDB, author: str | None = None, passages: int = 4) -> list[str]:
    await db.create_vertex(
        "document",
        {
            "id": "doc-1",
            "title": "Test Book",
            "author": author,
            "kind": "book",
            "source_type": "pdf",
            "ingested_at": "2026-07-21",
            "trace": "test",
        },
    )
    ids = []
    for position in range(passages):
        passage_id = f"p{position}"
        await db.create_vertex(
            "passage",
            {
                "id": passage_id,
                "document_id": "doc-1",
                "position": position,
                "content": f"passage {position}",
                "created_at": "2026-07-21",
            },
        )
        ids.append(passage_id)
    return ids


def _candidate(claim: str, description: str = "", score: int = 80) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=InvariantCandidate(
            claim=claim,
            description=description or claim,
            example="1979",
            condition=[Predicate(signal="real_rate", feature="level", op="<", value=0.0)],
            effect=Effect(
                handle="asset-class:gold-commodities",
                metric="return",
                method="cross_class",
                direction="outperform",
            ),
            scores=CandidateScores(
                generalizability=score,
                testability=score,
                actionability=score,
                evidence_quality=score,
                novelty=score,
                temporal_robustness=score,
            ),
            weight_initial=0.9,
        ),
        interest_score=float(score),
        rejection=None,
    )


async def _count(db: InvestmentDB, table: str, **where: Any) -> int:
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "1=1"
    rows = await db.query(f"SELECT COUNT(*) AS n FROM {table} WHERE {clause}", **where)
    return int(rows[0]["n"])


# -- idempotence ------------------------------------------------------------


async def test_uncurated_passages_shrinks_as_batches_land(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)

    assert len(await writeback.uncurated_passages("doc-1", FINGERPRINT)) == 4
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids[:2],
        scored=[_candidate("real rates below zero favour gold")],
        notes=[],
    )
    remaining = await writeback.uncurated_passages("doc-1", FINGERPRINT)
    assert [row["id"] for row in remaining] == passage_ids[2:]


async def test_a_new_fingerprint_re_exposes_every_passage(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """A prompt-version bump MUST re-curate — that is what makes the exclusion
    of the signal registry from the fingerprint safe."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[],
        notes=[],
    )
    assert await writeback.uncurated_passages("doc-1", FINGERPRINT) == []
    other = curation_fingerprint("test-model", "xhigh")
    assert len(await writeback.uncurated_passages("doc-1", other)) == 4


async def test_replaying_the_same_batch_creates_no_second_invariant(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """The defect this module fixes, stated as a test: the curator has three
    callers, so the same batch WILL be offered twice."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    batch = [_candidate("real rates below zero favour gold")]
    for _ in range(2):
        await writeback.persist_batch(
            document_id="doc-1",
            fingerprint=FINGERPRINT,
            passage_ids=passage_ids,
            scored=batch,
            notes=[],
        )
    assert await _count(db, "invariant") == 1


# -- deduplication ----------------------------------------------------------


async def test_paraphrases_merge_into_one_invariant_and_accrue_evidence(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """The measured failure of the 2026-07-21 run: six near-identical
    `real_rate < 0 -> gold` candidates across independent batches."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=[passage_ids[0]],
        scored=[_candidate("negative real interest rates cause gold to outperform")],
        notes=[],
    )
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=[passage_ids[1]],
        scored=[_candidate("when real rates turn negative, gold outperforms other assets")],
        notes=[],
    )
    assert await _count(db, "invariant") == 1
    # The duplicate is not dropped: its passage now SUPPORTS the survivor.
    assert await _count(db, "supports") == 2


async def test_a_distinct_claim_is_not_swallowed_by_the_dedup_gate(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[
            _candidate("negative real interest rates cause gold to outperform"),
            _candidate("a flat yield curve precedes equity drawdowns within two quarters"),
        ],
        notes=[],
    )
    assert await _count(db, "invariant") == 2


async def test_opposite_claims_are_never_merged_however_alike_they_read(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """The regression that made the structural guard necessary.

    Measured 2026-07-21: these two sit at cosine 0.907 — HIGHER than a genuine
    paraphrase pair (0.857) — because sentence embeddings encode vocabulary,
    not negation. A pure-cosine gate merges them and silently destroys one of
    two opposite invariants. Their conditions are provably disjoint, so the
    structure says no whatever the prose says."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    wide = _candidate("wide credit spreads precede equity underperformance")
    tight = _candidate("tight credit spreads precede equity outperformance")
    object.__setattr__(
        wide.candidate,
        "condition",
        [Predicate(signal="credit_spread", feature="level", op=">", value=3.0)],
    )
    object.__setattr__(
        tight.candidate,
        "condition",
        [Predicate(signal="credit_spread", feature="level", op="<", value=2.0)],
    )
    object.__setattr__(
        wide.candidate,
        "effect",
        Effect(
            handle="asset-class:equities",
            metric="return",
            method="cross_class",
            direction="underperform",
        ),
    )
    object.__setattr__(
        tight.candidate,
        "effect",
        Effect(
            handle="asset-class:equities",
            metric="return",
            method="cross_class",
            direction="outperform",
        ),
    )
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[wide, tight],
        notes=[],
    )
    assert await _count(db, "invariant") == 2


async def test_reference_knowledge_is_never_a_merge_target(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """A note has no condition and no effect, so nothing but wording could
    justify absorbing a weighted candidate into it."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=[passage_ids[0]],
        scored=[],
        notes=[
            ReferenceNote(
                claim="negative real interest rates cause gold to outperform",
                description="negative real interest rates cause gold to outperform",
                why_not_reducible="stated as narrative, no threshold given",
            )
        ],
    )
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=[passage_ids[1]],
        scored=[_candidate("when real rates turn negative, gold outperforms other assets")],
        notes=[],
    )
    rows = await db.query("SELECT condition FROM invariant ORDER BY created_at")
    assert len(rows) == 2
    assert rows[0]["condition"] == "[]"
    assert rows[1]["condition"] != "[]"


async def test_the_higher_scoring_paraphrase_is_the_one_kept(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """Descending-score order IS the tie-break: the strongest of a family is
    written first, so the weaker twins merge into it rather than the reverse."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[
            _candidate("negative real interest rates cause gold to outperform", score=40),
            _candidate("when real rates turn negative, gold outperforms other assets", score=90),
        ],
        notes=[],
    )
    rows = await db.query("SELECT title, trace FROM invariant")
    assert len(rows) == 1
    assert "90.0" in rows[0]["trace"]


# -- what gets persisted ----------------------------------------------------


async def test_reference_notes_persist_as_unconfrontable_invariants(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """DATA_MODELS.md: a ponctual fact is NOT a new entity — it is an Invariant
    with empty condition and no effect. All 50 of these were lost in the
    2026-07-21 run."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[],
        notes=[
            ReferenceNote(
                claim="Wealth gaps precede political upheaval",
                description="Dalio's big-cycle observation",
                why_not_reducible="no wealth-distribution series in the signal registry",
            )
        ],
    )
    rows = await db.query("SELECT condition, effect, tags FROM invariant")
    assert len(rows) == 1
    assert rows[0]["condition"] == "[]"
    assert rows[0]["effect"] is None
    assert "reference-knowledge" in rows[0]["tags"]


async def test_author_tier_sets_the_floor_and_binds_the_proposed_weight(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """The model proposes 0.9; the 'other' band caps it at 0.70 (seeded)."""
    passage_ids = await _document(db, author="Meb Faber")
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[_candidate("real rates below zero favour gold")],
        notes=[],
    )
    row = (await db.query("SELECT author, floor_weight, weight_initial FROM invariant"))[0]
    assert row["author"] is None
    assert row["floor_weight"] == 0.20
    assert row["weight_initial"] == 0.70


async def test_a_dalio_document_lands_in_the_dalio_tier(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    passage_ids = await _document(db, author="Ray Dalio")
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[_candidate("real rates below zero favour gold")],
        notes=[],
    )
    row = (await db.query("SELECT author, floor_weight FROM invariant"))[0]
    assert row["author"] == "dalio"
    assert row["floor_weight"] == 0.40


async def test_candidates_enter_proposed_never_integrated(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """ADR-006: belief does not grant integration, history does."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[_candidate("real rates below zero favour gold", score=100)],
        notes=[],
    )
    rows = await db.query("SELECT status, validated_at FROM invariant")
    assert rows[0]["status"] == "proposed"
    assert rows[0]["validated_at"] is None


async def test_eventlog_precedes_the_invariant_it_explains(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """CLAUDE.md: every UC side-effect is appended to EventLog BEFORE its
    vertex commit, same transaction."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[_candidate("real rates below zero favour gold")],
        notes=[],
    )
    events = await db.query("SELECT type, source_uc, payload FROM event_log")
    assert len(events) == 1
    assert events[0]["type"] == "KnowledgeEvent"
    assert events[0]["source_uc"] == "UC4"


async def test_a_cited_passage_outside_the_batch_is_not_edged(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """A model that invents a passage id must not create a dangling SUPPORTS
    edge — the FK would accept anything the row happens to name."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    item = _candidate("real rates below zero favour gold")
    item.candidate.supporting_passages.extend(["p0", "p-invented"])
    await writeback.persist_batch(
        document_id="doc-1",
        fingerprint=FINGERPRINT,
        passage_ids=passage_ids,
        scored=[item],
        notes=[],
    )
    edges = await db.query("SELECT passage_id FROM supports")
    assert [edge["passage_id"] for edge in edges] == ["p0"]


# -- resumability -----------------------------------------------------------


async def test_concurrent_batches_persist_without_clobbering_each_other(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """`curate_document` fans out four batches at a time, so persistence is
    ALWAYS reached concurrently — every sequential test above tested a path
    production never takes.

    Measured before the lock existed (2026-07-21): one batch committed, the
    other raised `cannot start a transaction within a transaction` — ADR-004
    gives the agent a single connection, and two overlapping `transaction()`
    blocks issue two BEGINs on it. In a real run that exception is swallowed
    by the curator's batch-level `except` and logged as a warning, so three
    batches in four would have been lost while the run reported success.

    Pinned here for both properties the lock buys: nothing raises, and the
    cross-batch dedup still sees what the other batch committed."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)

    async def persist(passage_id: str, claim: str) -> Any:
        return await writeback.persist_batch(
            document_id="doc-1",
            fingerprint=FINGERPRINT,
            passage_ids=[passage_id],
            scored=[_candidate(claim)],
            notes=[],
        )

    results = await asyncio.gather(
        persist(passage_ids[0], "negative real interest rates cause gold to outperform"),
        persist(passage_ids[1], "when real rates turn negative, gold outperforms other assets"),
        persist(passage_ids[2], "a flat yield curve precedes equity drawdowns within two quarters"),
        return_exceptions=True,
    )
    assert [r for r in results if isinstance(r, BaseException)] == []
    # Two paraphrases + one distinct claim, across three concurrent batches:
    # the dedup must hold ACROSS them, not merely within one.
    assert await _count(db, "invariant") == 2
    assert await _count(db, "curated_passage") == 3


async def test_a_failed_batch_leaves_its_passages_uncurated(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """Atomicity, stated as the property that matters: a passage is never
    marked curated unless its knowledge committed in the same transaction."""
    passage_ids = await _document(db)
    writeback = KnowledgeWriteback(db, embedder)
    broken = _candidate("real rates below zero favour gold")
    # An unpersistable candidate: title is NOT NULL in the schema.
    object.__setattr__(broken.candidate, "claim", None)

    with pytest.raises(sqlite3.IntegrityError):
        await writeback.persist_batch(
            document_id="doc-1",
            fingerprint=FINGERPRINT,
            passage_ids=passage_ids,
            scored=[broken],
            notes=[],
        )
    assert await _count(db, "curated_passage") == 0
    assert await _count(db, "invariant") == 0
    assert await _count(db, "event_log") == 0


def test_author_tier_defaults_to_the_conservative_other_bucket() -> None:
    assert author_tier("Ray Dalio") == "dalio"
    assert author_tier("Howard Marks") == "marks"
    assert author_tier("Meb Faber") is None
    assert author_tier(None) is None
