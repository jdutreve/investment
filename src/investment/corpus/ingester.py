"""Corpus ingestion — file in, Document + Passages + SUPPORTS out (Task 3.1).

ONE pipeline serves both entry points (docs/TASKS.md Task 3.1: "single
pipeline: watcher AND UC0 seed"), so a book dropped in the inbox and a book
seeded at UC0 produce byte-identical rows.

PERSISTENCE ORDER is fixed and load-bearing (CLAUDE.md EventLog rule): the
IngestionEvent is appended BEFORE the Document and Passage vertices, all inside
ONE transaction. A crash therefore leaves either nothing or a complete document
— never passages whose ingestion was never recorded.

EXTRACTION QUALITY is the reason `normalize_whitespace` exists and runs first.
Measured on the real corpus (2026-07-20): pypdf emits ~1.6-2.2% "orphan
letters" — words split mid-token by kerning ("A ll", "d ecline", "differentia
l") — plus hard-wrapped lines and hyphenated line breaks. Separately, a PDF
converted to markdown by a bad converter can arrive with every word on its own
line. Both degrade embeddings silently rather than loudly, so the pipeline
normalizes before it chunks, and every source type goes through the same
normalizer.

DEFERRED by design, not forgotten: `.url` (fetch + boilerplate strip) and
kindle `.csv` are in the Task 3.1 spec but have no source to test against yet,
and CLAUDE.md forbids speculative stubs. `SUPPORTED_SUFFIXES` names exactly
what works; anything else raises `UnsupportedSourceError` with the suffix, so
the watcher can route the file to `inbox/failed/` with a real reason.
"""

import hashlib
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from investment.corpus.embedding import (
    InProcessEmbedder,
    cosine_matrix,
    invariant_embedding_input,
    to_blob,
)
from investment.db.sqlite import InvestmentDB

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = frozenset({".pdf", ".txt", ".md"})

# Chunking defaults. The DB `system_thresholds` rows are authoritative at
# runtime (seed_data.py: chunk_size_chars / chunk_overlap_chars); these are the
# fallbacks used when a caller builds an ingester without a DB-loaded config.
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150
DEFAULT_SIMILARITY_MIN = 0.35

# A page yielding less than this after normalization carries no prose worth
# embedding. Calibrated on the real books rather than guessed: prose pages run
# a median of 4694 chars in "The Changing World Order" and 2838 in "Big Debt
# Crises", while chart-only pages ("United Kingdom 1941-1967 Chart Deck
# Appendix / Government and Military") yield 120-150. Two orders of magnitude
# separate the two populations, so any threshold in this gap is robust; 300
# sits in it. Big Debt Crises is 27% such pages — without this floor they
# become hundreds of passages that are semantically empty but dense in
# financial vocabulary, which is the worst case: they match seeded invariants
# on cosine and pollute SUPPORTS while spending curator tokens on nothing.
MIN_PAGE_CHARS = 300

EXCERPT_CHARS = 100


class UnsupportedSourceError(ValueError):
    """Raised for a suffix outside SUPPORTED_SUFFIXES — carries the suffix so
    the caller can report WHY a file was rejected, not just that it was."""


@dataclass(frozen=True)
class Chunk:
    """One passage before persistence. `page` is None for formats with no
    pagination (.txt/.md) — the schema column is nullable for exactly that."""

    position: int
    page: int | None
    content: str


@dataclass(frozen=True)
class IngestResult:
    document_id: str
    title: str
    chunk_count: int
    supports_created: int
    pages_skipped: int


# -- pure text handling (no I/O, no DB — unit-testable on strings) ----------


def normalize_whitespace(text: str) -> str:
    """Collapse extraction noise into clean prose. Runs before chunking on
    EVERY source type.

    Four passes, in this order (each undoes damage the next would otherwise
    bake in):
      1. Unicode NFKC + non-breaking spaces -> plain spaces, so ligatures and
         exotic spaces do not survive as their own tokens.
      2. De-hyphenate across line breaks ("deleverag-\\ning" -> "deleveraging")
         — must precede the newline collapse or the hyphen is stranded.
      3. Collapse single newlines to spaces while PRESERVING blank-line
         paragraph breaks; this is what repairs a word-per-line markdown
         conversion without destroying paragraph structure.
      4. Squeeze runs of spaces.

    Deliberately NOT repaired: the "A ll" / "differentia l" orphan-letter
    noise. Rejoining a stray single letter to its neighbour is unsafe — it
    would corrupt legitimate text ("a lot" -> "alot", initials, "I think").
    At 1.6-2.2% of tokens the residue is well within what a sentence
    transformer absorbs, so the honest choice is to leave it and say so."""
    text = unicodedata.normalize("NFKC", text)
    # Escaped, not literal: a raw NBSP/ZWSP in source is invisible to review.
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"\n{2,}", "\x00", text)  # protect paragraph breaks
    text = text.replace("\n", " ")
    text = text.replace("\x00", "\n\n")
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def chunk_text(
    text: str,
    *,
    page: int | None,
    start_position: int,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """Sliding window of `size` chars stepping `size - overlap`, cut on a word
    boundary so no passage begins or ends mid-word.

    The overlap is what keeps a claim that straddles a boundary retrievable:
    without it, a sentence split across two chunks is embedded as two halves
    and matches neither query well."""
    if size <= overlap:
        raise ValueError(f"chunk size {size} must exceed overlap {overlap}")
    text = text.strip()
    if not text:
        return []
    chunks: list[Chunk] = []
    start = 0
    position = start_position
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Back off to the last space so the window ends on a whole word.
            space = text.rfind(" ", start, end)
            if space > start:
                end = space
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(position=position, page=page, content=piece))
            position += 1
        if end >= len(text):
            break
        # Both EDGES must land on a word boundary, not just `end`. Stepping
        # back a raw `overlap` characters lands mid-word, so the next chunk
        # would BEGIN with a fragment ("...ing rates" -> "ng rates") — the end
        # boundary above cannot prevent that. Rewind to the space preceding the
        # tentative start and resume just after it: the overlap is then at
        # least `overlap` chars, never less, and always whole words.
        tentative = max(end - overlap, start + 1)
        boundary = text.rfind(" ", start, tentative)
        start = boundary + 1 if boundary > start else tentative
    return chunks


def chunk_id_for(document_id: str, position: int) -> str:
    """Stable, content-independent id: re-ingesting the same file overwrites
    the same passage rows (UPSERT by id) instead of duplicating the corpus."""
    return f"{document_id}:{position:05d}"


def document_id_for(path: Path, title: str) -> str:
    """Derived from the TITLE, not the filesystem path, so the same book
    re-deposited under a different filename is recognised as the same document.
    Short hash keeps it readable in logs and stable across machines."""
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
    return f"doc-{digest}"


def title_from(path: Path) -> str:
    """Filename without suffix, lightly cleaned — separators to spaces and
    squeezed. Deliberately not parsed from PDF metadata: that field is empty or
    wrong in most real-world books, and a silently wrong title becomes a wrong
    `document_id`."""
    stem = path.stem.replace("_", " ").replace("-", " ")
    return re.sub(r"\s{2,}", " ", stem).strip()


# -- extraction ------------------------------------------------------------


def extract_pages(path: Path) -> tuple[list[tuple[int | None, str]], int]:
    """`([(page_number_or_None, normalized_text)], pages_skipped)`.

    PDFs yield one entry per page (1-indexed, matching what a reader sees);
    .txt/.md yield a single entry with `page=None`. Pages under
    MIN_PAGE_CHARS are dropped and counted — see that constant for why."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedSourceError(f"unsupported source type {suffix!r} for {path.name}")
    if suffix in (".txt", ".md"):
        return [(None, normalize_whitespace(path.read_text(encoding="utf-8", errors="replace")))], 0

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[tuple[int | None, str]] = []
    skipped = 0
    for index, page in enumerate(reader.pages, start=1):
        text = normalize_whitespace(page.extract_text() or "")
        if len(text) < MIN_PAGE_CHARS:
            skipped += 1
            continue
        pages.append((index, text))
    return pages, skipped


# -- the ingester ----------------------------------------------------------


class CorpusIngester:
    """Turns a file into a Document + its Passages + SUPPORTS edges."""

    def __init__(
        self,
        db: InvestmentDB,
        embedder: InProcessEmbedder,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        similarity_min: float = DEFAULT_SIMILARITY_MIN,
    ) -> None:
        self._db = db
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._similarity_min = similarity_min

    @classmethod
    async def from_db(cls, db: InvestmentDB, embedder: InProcessEmbedder) -> "CorpusIngester":
        """Build with the CALIBRATED thresholds from `system_thresholds` rather
        than the module defaults — the DB is authoritative at runtime."""
        rows = await db.query("SELECT key, value FROM system_thresholds")
        cfg = {r["key"]: r["value"] for r in rows}
        return cls(
            db,
            embedder,
            chunk_size=int(cfg.get("chunk_size_chars", DEFAULT_CHUNK_SIZE)),
            chunk_overlap=int(cfg.get("chunk_overlap_chars", DEFAULT_CHUNK_OVERLAP)),
            similarity_min=float(cfg.get("vector_similarity_min", DEFAULT_SIMILARITY_MIN)),
        )

    def build_chunks(self, pages: Sequence[tuple[int | None, str]]) -> list[Chunk]:
        """Chunk page by page so a passage never straddles a page boundary —
        which is what keeps `passage.page` a truthful citation rather than an
        approximation."""
        chunks: list[Chunk] = []
        for page, text in pages:
            chunks.extend(
                chunk_text(
                    text,
                    page=page,
                    start_position=len(chunks),
                    size=self._chunk_size,
                    overlap=self._chunk_overlap,
                )
            )
        return chunks

    async def ingest_file(
        self, path: Path, *, kind: str = "book", author: str | None = None
    ) -> IngestResult:
        """Extract -> normalize -> chunk -> embed -> persist, then link
        SUPPORTS. Idempotent: re-ingesting the same title overwrites the same
        document and passage ids."""
        title = title_from(path)
        document_id = document_id_for(path, title)
        pages, pages_skipped = extract_pages(path)
        chunks = self.build_chunks(pages)
        if not chunks:
            raise ValueError(f"{path.name} produced no passages after normalization")

        # CPU-bound and slow (hundreds of vectors) — but this is a batch job on
        # the ingestion path, not the asyncio chain, so it runs inline rather
        # than behind an executor hop that would buy nothing here.
        vectors = self._embedder.encode([c.content for c in chunks])
        logger.info("ingest %s: %d passages, %d pages skipped", title, len(chunks), pages_skipped)

        now = datetime.now(UTC).isoformat()
        async with self._db.transaction() as tx:
            # EventLog FIRST, same transaction (CLAUDE.md).
            await tx.append_event(
                "IngestionEvent",
                source_uc="UC4",
                source_id=document_id,
                payload={
                    "document_id": document_id,
                    "title": title,
                    "source_path": str(path),
                    "chunk_count": len(chunks),
                    "pages_skipped": pages_skipped,
                },
            )
            await tx.upsert_vertex(
                "document",
                document_id,
                {
                    "title": title,
                    "author": author,
                    "kind": kind,
                    "source_type": "pdf" if path.suffix.lower() == ".pdf" else "text",
                    "source_path": str(path),
                    "ingested_at": now,
                    "chunk_count": len(chunks),
                    "trace": f"Ingested from {path.name} ({len(chunks)} passages, "
                    f"{pages_skipped} low-content pages skipped).",
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                passage_id = chunk_id_for(document_id, chunk.position)
                await tx.upsert_vertex(
                    "passage",
                    passage_id,
                    {
                        "document_id": document_id,
                        "position": chunk.position,
                        "page": chunk.page,
                        "content": chunk.content,
                        "chunk_id": passage_id,
                        "embedding": to_blob(vector),
                    },
                )

        supports = await self.link_supports(document_id, chunks, vectors)
        return IngestResult(
            document_id=document_id,
            title=title,
            chunk_count=len(chunks),
            supports_created=supports,
            pages_skipped=pages_skipped,
        )

    async def link_supports(
        self, document_id: str, chunks: Sequence[Chunk], vectors: np.ndarray
    ) -> int:
        """SUPPORTS edge wherever a passage's cosine to an invariant clears
        `vector_similarity_min`.

        Invariants are embedded here, in RAM, from `invariant_embedding_input`
        — the SAME pinned text the Planner will use, so the two sides of the
        comparison cannot drift (corpus/embedding.py)."""
        invariants = await self._db.query(
            "SELECT id, title, description FROM invariant ORDER BY id"
        )
        if not invariants or not len(vectors):
            return 0
        matrix = self._embedder.encode(
            [invariant_embedding_input(str(r["title"]), str(r["description"])) for r in invariants]
        )
        similarities = cosine_matrix(vectors, matrix)
        created = 0
        async with self._db.transaction() as tx:
            for row_index, chunk in enumerate(chunks):
                passage_id = chunk_id_for(document_id, chunk.position)
                for col_index, invariant in enumerate(invariants):
                    score = float(similarities[row_index][col_index])
                    if score < self._similarity_min:
                        continue
                    await tx.create_edge(
                        "supports",
                        passage_id,
                        str(invariant["id"]),
                        {"strength": score, "excerpt": chunk.content[:EXCERPT_CHARS]},
                    )
                    created += 1
        return created
