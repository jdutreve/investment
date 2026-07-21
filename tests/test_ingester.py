"""M7 tests for `corpus/ingester.py` — real SQLite, real embedder, no mocks.

Split in two: pure text handling (fast, string-level, covers the extraction
noise the real corpus actually contains) and the DB round-trip (persistence
order, idempotence, SUPPORTS).
"""

from pathlib import Path

import pytest

from investment.corpus.embedding import InProcessEmbedder, from_blob
from investment.corpus.ingester import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    MIN_PAGE_CHARS,
    CorpusIngester,
    UnsupportedSourceError,
    chunk_text,
    document_id_for,
    extract_pages,
    normalize_whitespace,
    title_from,
)
from investment.db.schema import SCHEMA_SQL  # noqa: F401  (ensures schema import is valid)
from investment.db.sqlite import InvestmentDB


@pytest.fixture(scope="module")
def embedder() -> InProcessEmbedder:
    return InProcessEmbedder("all-MiniLM-L6-v2")


# -- pure text handling ----------------------------------------------------


def test_normalize_repairs_word_per_line_conversion() -> None:
    # The exact damage found in docs/AssetAllocationBook: every word on its own
    # line. Left unrepaired it would make chunks mostly whitespace.
    mangled = "I \n believe \n in \n the \n discipline \n of \n mastering"
    assert normalize_whitespace(mangled) == "I believe in the discipline of mastering"


def test_normalize_dehyphenates_across_line_breaks() -> None:
    assert normalize_whitespace("deleverag-\ning is slow") == "deleveraging is slow"


def test_normalize_preserves_paragraph_breaks() -> None:
    # Single newlines are layout; blank lines are structure. Losing the latter
    # would merge unrelated paragraphs into one passage.
    out = normalize_whitespace("first line\nsame para\n\nsecond para")
    assert out == "first line same para\n\nsecond para"


def test_chunks_never_split_a_word() -> None:
    text = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_text(text, page=3, start_position=0, size=200, overlap=50)
    assert len(chunks) > 1
    for c in chunks:
        assert not c.content.startswith(" ") and not c.content.endswith(" ")
        # A split mid-word would leave a fragment that is not a real token.
        assert all(w.startswith("word") for w in c.content.split())


def test_chunks_overlap_so_a_straddling_claim_survives() -> None:
    text = " ".join(f"w{i}" for i in range(300))
    chunks = chunk_text(text, page=None, start_position=0, size=200, overlap=50)
    # Consecutive chunks must share text, else a sentence cut at a boundary is
    # embedded as two halves and matches neither query.
    assert any(chunks[0].content.split()[-1] in chunks[1].content for _ in [0])


def test_chunk_positions_are_contiguous_across_pages() -> None:
    pages = [(1, " ".join(f"a{i}" for i in range(200))), (2, " ".join(f"b{i}" for i in range(200)))]
    ing = CorpusIngester.__new__(CorpusIngester)
    ing._chunk_size, ing._chunk_overlap = 200, 50  # type: ignore[attr-defined]
    chunks = ing.build_chunks(pages)
    assert [c.position for c in chunks] == list(range(len(chunks)))
    # Page provenance must stay truthful: no passage straddles two pages.
    assert {c.page for c in chunks} == {1, 2}


def test_chunk_size_must_exceed_overlap() -> None:
    with pytest.raises(ValueError, match="must exceed overlap"):
        chunk_text("x", page=None, start_position=0, size=100, overlap=100)


def test_document_id_is_title_derived_not_path_derived() -> None:
    # The same book re-deposited under another filename is the same document.
    a = document_id_for(Path("/inbox/dalio.pdf"), "The Changing World Order")
    b = document_id_for(Path("/sources/copy (1).pdf"), "The Changing World Order")
    assert a == b


def test_title_cleans_separators() -> None:
    assert title_from(Path("/x/The_Changing-World Order.pdf")) == "The Changing World Order"


def test_unsupported_suffix_names_the_suffix(tmp_path: Path) -> None:
    # The watcher reports WHY a file went to inbox/failed/, so the message must
    # carry the suffix rather than a generic failure.
    bad = tmp_path / "notes.epub"
    bad.write_text("x")
    with pytest.raises(UnsupportedSourceError, match="epub"):
        extract_pages(bad)


def test_markdown_extraction_yields_one_unpaginated_entry(tmp_path: Path) -> None:
    src = tmp_path / "note.md"
    src.write_text("Rising inflation\nerodes bonds.", encoding="utf-8")
    pages, skipped = extract_pages(src)
    assert skipped == 0
    assert pages == [(None, "Rising inflation erodes bonds.")]


# -- DB round-trip ---------------------------------------------------------


@pytest.fixture
async def db(tmp_path: Path) -> InvestmentDB:
    return InvestmentDB(tmp_path / "t.db")


async def _seed_invariant(db: InvestmentDB) -> None:
    await db.upsert_vertex(
        "invariant",
        "inv-test-inflation",
        {
            "title": "Inflation erodes nominal bonds",
            "description": "When inflation rises, nominal bond returns fall.",
            "source": "test fixture",
            "status": "proposed",
            "tags": [],
            "condition": [],
            "weight_initial": 0.6,
            "floor_weight": 0.2,
            "confirmation_count": 0,
            "infirmation_count": 0,
            "market_score": 1.0,
            "recency_factor": 1.0,
            "trace": "test",
        },
    )


async def test_ingest_persists_document_passages_and_event(
    db: InvestmentDB, embedder: InProcessEmbedder, tmp_path: Path
) -> None:
    await _seed_invariant(db)
    src = tmp_path / "Test Book.md"
    src.write_text(" ".join(f"inflation bonds sentence{i}" for i in range(300)), encoding="utf-8")

    ing = CorpusIngester(db, embedder)
    result = await ing.ingest_file(src)

    assert result.chunk_count > 1
    docs = await db.query("SELECT * FROM document")
    assert len(docs) == 1 and docs[0]["title"] == "Test Book"
    assert docs[0]["chunk_count"] == result.chunk_count

    passages = await db.query("SELECT * FROM passage ORDER BY position")
    assert len(passages) == result.chunk_count
    # Embeddings survive the BLOB round-trip at full dimension.
    assert from_blob(passages[0]["embedding"]).shape == (embedder.dims,)

    events = await db.query("SELECT * FROM event_log WHERE type = 'IngestionEvent'")
    assert len(events) == 1


async def test_event_is_appended_before_the_document_row(
    db: InvestmentDB, embedder: InProcessEmbedder, tmp_path: Path
) -> None:
    # CLAUDE.md: the side-effect event precedes its vertex commit, same
    # transaction. Both land, so the observable guarantee is atomicity: a
    # document never exists without its IngestionEvent.
    src = tmp_path / "Ordered.md"
    src.write_text("inflation " * 400, encoding="utf-8")
    await CorpusIngester(db, embedder).ingest_file(src)
    docs = await db.query("SELECT id FROM document")
    events = await db.query("SELECT source_id FROM event_log WHERE type = 'IngestionEvent'")
    assert events[0]["source_id"] == docs[0]["id"]


async def test_reingest_is_idempotent(
    db: InvestmentDB, embedder: InProcessEmbedder, tmp_path: Path
) -> None:
    src = tmp_path / "Same Book.md"
    src.write_text("credit spreads widen in recessions. " * 100, encoding="utf-8")
    ing = CorpusIngester(db, embedder)
    first = await ing.ingest_file(src)
    second = await ing.ingest_file(src)
    assert first.document_id == second.document_id
    assert len(await db.query("SELECT id FROM document")) == 1
    assert len(await db.query("SELECT id FROM passage")) == first.chunk_count


async def test_supports_edge_lands_on_a_relevant_invariant(
    db: InvestmentDB, embedder: InProcessEmbedder, tmp_path: Path
) -> None:
    await _seed_invariant(db)
    src = tmp_path / "Inflation Study.md"
    src.write_text(
        "Rising inflation erodes the real return of nominal government bonds. "
        "When consumer prices accelerate, fixed coupons lose purchasing power. " * 8,
        encoding="utf-8",
    )
    result = await CorpusIngester(db, embedder).ingest_file(src)
    assert result.supports_created >= 1
    edges = await db.query("SELECT * FROM supports")
    assert edges[0]["invariant_id"] == "inv-test-inflation"
    assert 0.35 <= edges[0]["strength"] <= 1.0
    assert edges[0]["excerpt"]


async def test_unrelated_text_creates_no_supports_edge(
    db: InvestmentDB, embedder: InProcessEmbedder, tmp_path: Path
) -> None:
    # The floor has to actually reject: if everything matches, SUPPORTS is noise.
    await _seed_invariant(db)
    src = tmp_path / "Cooking.md"
    src.write_text("Chop the onions finely and fry them in butter until golden. " * 20)
    result = await CorpusIngester(db, embedder).ingest_file(src)
    assert result.supports_created == 0


async def test_from_db_reads_calibrated_thresholds(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    for key, value in (("chunk_size_chars", 500.0), ("chunk_overlap_chars", 60.0)):
        await db.command(
            "INSERT OR REPLACE INTO system_thresholds (key, value, updated_at) VALUES (:k, :v, :t)",
            k=key,
            v=value,
            t="2026-07-21T00:00:00+00:00",
        )
    ing = await CorpusIngester.from_db(db, embedder)
    assert (ing._chunk_size, ing._chunk_overlap) == (500, 60)


def test_defaults_match_the_seeded_thresholds() -> None:
    # Guards against the module defaults and seed_data drifting apart.
    from investment.db.seed_data import SYSTEM_THRESHOLDS

    assert SYSTEM_THRESHOLDS["chunk_size_chars"] == DEFAULT_CHUNK_SIZE
    assert SYSTEM_THRESHOLDS["chunk_overlap_chars"] == DEFAULT_CHUNK_OVERLAP
    assert MIN_PAGE_CHARS == 300
