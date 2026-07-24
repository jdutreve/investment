"""UC4 knowledge writeback — scored candidates in, persisted knowledge out.

The mechanical half of the curator loop: NO LLM, no judgment. The curator
PROPOSES (worker/curator.py); this module DISPOSES — the same propose/dispose
split UC8 uses, for the same reason: what reaches the database must be
decided by code the owner can read, not by a model's self-assessment.

WHY THIS MODULE EXISTS AT ALL (the defect it fixes): `curate_document` read
EVERY passage of a document on every call, and the curator has three callers
(inbox watcher, Monday 08:10 sweep, ad-hoc). Without a checkpoint, each call
would re-spend a full corpus run AND mint a fresh set of near-identical
invariants. Persistence without idempotence would have turned a wasteful
re-run into permanent duplication.

Three properties, in the order they matter:

1. IDEMPOTENT — a passage already curated under the same fingerprint is
   skipped. A Monday sweep over a stable corpus makes zero LLM calls.
2. RESUMABLE — written per batch as it returns, inside one transaction. A
   crash at 95% costs the batch in flight, not the run.
3. DEDUPLICATED — a candidate that restates one already in the DB attaches
   its evidence (SUPPORTS) instead of becoming a second invariant. Measured
   need, not a precaution: the 2026-07-21 full-corpus run produced SIX
   variants of "real_rate < 0 -> gold outperforms" among its top 15, because
   each batch is an independent call with no memory of the others.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from investment.corpus.embedding import (
    InProcessEmbedder,
    cosine_matrix,
    from_blob,
    invariant_embedding_input,
    to_blob,
)
from investment.db.sqlite import InvestmentDB
from investment.mechanical.invariants import conditions_can_overlap
from investment.worker.curator import ReferenceNote, ScoredCandidate

logger = logging.getLogger(__name__)

# Prose similarity is the FIRST of two conditions for a merge, never the only
# one. 0.80 on L2-normalised MiniLM embeddings, measured 2026-07-21:
#   0.857  "negative real rates cause gold to outperform"
#       vs "when real rates turn negative, gold outperforms"     -> same claim
#   0.547  ... vs "negative real rates favour equities over bonds"
#   0.124  ... vs "a flat yield curve precedes equity drawdowns"
# But also, and this is why the threshold alone is NOT a dedup rule:
#   0.907  "WIDE credit spreads precede equity UNDERperformance"
#       vs "TIGHT credit spreads precede equity OUTperformance"  -> OPPOSITE
# Sentence embeddings encode vocabulary, not negation: two inverse invariants
# share nearly every word and land higher than a genuine paraphrase pair. A
# false merge is strictly worse than a duplicate — a duplicate is visible and
# cleanable, a merge destroys a claim silently — so the structural agreement
# below is what actually authorises it.
#
# Lowered 0.80 -> 0.75 after the ice core (2026-07-21) measured a real missed
# duplicate at 0.782: the same claim ("growth falling, inflation rising -> gold
# outperforms"), same effect, written twice with different wording, persisted
# twice. 0.80 was a prior fitted to four hand-built pairs; 0.782 is the first
# number from actual curator output. Lowering was unsafe while cosine decided
# alone — it is much less so now that a merge ALSO requires structural
# agreement, which is what holds the 0.907 wide-vs-tight pair apart whatever
# the threshold says.
DEDUP_COSINE_THRESHOLD = 0.75

# document.author -> invariant author tier (floors: dalio 0.40, marks 0.35,
# null 0.20, system 0.05 — CLAUDE.md "Invariant weight model"). Substring
# match, lowercased, because `document.author` is a human name ("Ray Dalio")
# while the tier is a corpus identity. Anything unmatched is the 'other' tier
# (author=NULL, floor 0.20) — the conservative default, per DATA_MODELS.md:
# "Invariants extracted from UC3 events or user notes carry author=null".
AUTHOR_TIERS: dict[str, str] = {"dalio": "dalio", "marks": "marks"}

# The fields that make two effects the same effect.
_EFFECT_FIELDS = ("handle", "metric", "method", "direction")


def author_tier(document_author: str | None) -> str | None:
    if not document_author:
        return None
    lowered = document_author.lower()
    for needle, tier in AUTHOR_TIERS.items():
        if needle in lowered:
            return tier
    return None


@dataclass(frozen=True)
class WritebackReport:
    """What one persisted batch actually changed — the numbers the digest and
    the M7 STOP inspection read."""

    created: int = 0
    merged: int = 0
    notes_created: int = 0
    passages_marked: int = 0
    demoted: int = 0

    def __add__(self, other: "WritebackReport") -> "WritebackReport":
        return WritebackReport(
            created=self.created + other.created,
            merged=self.merged + other.merged,
            notes_created=self.notes_created + other.notes_created,
            passages_marked=self.passages_marked + other.passages_marked,
            demoted=self.demoted + other.demoted,
        )


class KnowledgeWriteback:
    """Persists curator output and maintains the curation checkpoint."""

    def __init__(
        self,
        db: InvestmentDB,
        embedder: InProcessEmbedder,
        *,
        dedup_threshold: float = DEDUP_COSINE_THRESHOLD,
    ) -> None:
        self._db = db
        self._embedder = embedder
        self._dedup_threshold = dedup_threshold
        # Serialises persistence across the CONCURRENT batches of
        # `curate_document`. Two reasons, one lock:
        #
        # 1. Correctness of the write itself. ADR-004 gives the agent ONE
        #    SQLite connection, so two overlapping `transaction()` blocks issue
        #    two BEGINs on it: "cannot start a transaction within a
        #    transaction". Measured 2026-07-21 with two concurrent batches —
        #    one committed, the other raised and lost its candidates. At
        #    MAX_CONCURRENT_CALLS=4 most of a corpus run would vanish into the
        #    curator's batch-level `except`, as warnings rather than failures.
        # 2. Correctness of the dedup. The comparison corpus is read INSIDE
        #    the lock (below), so a batch always sees what earlier batches
        #    committed. Reading it outside would let two batches both observe
        #    an empty corpus and both create the same invariant — the exact
        #    duplication the gate exists to prevent.
        self._lock = asyncio.Lock()

    # -- checkpoint --------------------------------------------------------

    async def uncurated_passages(self, document_id: str, fingerprint: str) -> list[dict[str, Any]]:
        """The passages this fingerprint has NOT yet seen, in reading order.

        An empty result means there is nothing to spend a token on — the
        caller skips the LLM entirely rather than calling it and discarding
        the answer."""
        return await self._db.query(
            "SELECT p.id, p.page, p.content FROM passage p "
            "WHERE p.document_id = :d AND NOT EXISTS ("
            "  SELECT 1 FROM curated_passage c "
            "  WHERE c.passage_id = p.id AND c.fingerprint = :f"
            ") ORDER BY p.position",
            d=document_id,
            f=fingerprint,
        )

    # -- persistence -------------------------------------------------------

    async def persist_batch(
        self,
        *,
        document_id: str,
        fingerprint: str,
        passage_ids: list[str],
        scored: list[ScoredCandidate],
        notes: list[ReferenceNote],
    ) -> WritebackReport:
        """Persist ONE batch's output and mark its passages curated, atomically.

        Atomicity is what makes the checkpoint trustworthy: a passage is never
        marked curated unless the knowledge it produced is committed in the
        same transaction. The alternative — mark first, write after — loses
        candidates permanently on a crash while claiming the work is done.

        Serialised by `self._lock`: safe to call from concurrent batches, and
        the only correct way to call it from them (see __init__).
        """
        admissible = sorted(
            (s for s in scored if s.admissible), key=lambda s: s.interest_score, reverse=True
        )
        demoted = len(scored) - len(admissible)
        report = WritebackReport(demoted=demoted)

        async with self._lock:
            report += await self._persist_locked(
                document_id, fingerprint, passage_ids, admissible, notes, len(scored)
            )

        logger.info(
            "writeback: %s +%d invariants, %d merged, %d notes, %d passages marked",
            document_id,
            report.created,
            report.merged,
            report.notes_created,
            report.passages_marked,
        )
        return report

    async def _persist_locked(
        self,
        document_id: str,
        fingerprint: str,
        passage_ids: list[str],
        admissible: list[ScoredCandidate],
        notes: list[ReferenceNote],
        candidate_count: int,
    ) -> WritebackReport:
        """The critical section. Everything that reads state the previous
        batch may have written lives here, inside the lock."""
        author = author_tier(await self._document_author(document_id))
        band = await self._author_band(author)
        corpus, corpus_matrix = await self._existing_embeddings()

        report = WritebackReport()
        async with self._db.transaction() as tx:
            # EventLog FIRST, same transaction (CLAUDE.md "EventLog"): the
            # audit spine must record the intent before the rows it explains.
            await tx.append_event(
                type="KnowledgeEvent",
                source_uc="UC4",
                source_id=document_id,
                payload={
                    "fingerprint": fingerprint,
                    "passages": len(passage_ids),
                    "candidates": candidate_count,
                    "admissible": len(admissible),
                    "demoted": candidate_count - len(admissible),
                    "reference_notes": len(notes),
                    "author_tier": author,
                },
            )

            for item in admissible:
                # Descending score IS the "keep the best" rule: the strongest
                # of a paraphrase family is written first, so its weaker twins
                # find it in the corpus and merge INTO it. No tie-break needed.
                vector = self._encode(item.candidate.claim, item.candidate.description)
                match = self._nearest(vector, item, corpus, corpus_matrix)
                if match is not None:
                    # Never overwrite the incumbent, whatever it scores: a
                    # persisted invariant may already carry confrontation
                    # history (market_score, counts), and a higher triage
                    # score is a PRIOR — it does not outrank measurement.
                    # The duplicate's evidence is not lost, it accrues.
                    await self._attach_evidence(tx, match, item, passage_ids)
                    report += WritebackReport(merged=1)
                    continue
                invariant_id = await self._create_invariant(tx, item, author, band, vector)
                await self._attach_evidence(tx, invariant_id, item, passage_ids)
                corpus.append(
                    _Existing(
                        id=invariant_id,
                        condition=[p.model_dump() for p in item.candidate.condition],
                        effect=item.candidate.effect.model_dump(),
                    )
                )
                corpus_matrix = (
                    vector.reshape(1, -1)
                    if corpus_matrix.size == 0
                    else np.vstack([corpus_matrix, vector])
                )
                report += WritebackReport(created=1)

            for note in notes:
                await self._create_reference_note(tx, note, document_id, author, passage_ids)
                report += WritebackReport(notes_created=1)

            for passage_id in passage_ids:
                await tx.command(
                    "INSERT OR REPLACE INTO curated_passage "
                    "(passage_id, fingerprint, curated_at, candidate_count) "
                    "VALUES (:p, :f, datetime('now'), :n)",
                    p=passage_id,
                    f=fingerprint,
                    n=len(admissible),
                )
            report += WritebackReport(passages_marked=len(passage_ids))

        return report

    # -- internals ---------------------------------------------------------

    def _encode(self, title: str, description: str) -> np.ndarray:
        text = invariant_embedding_input(title, description)
        vector: np.ndarray = self._embedder.encode([text])[0]
        return vector

    def _nearest(
        self,
        vector: np.ndarray,
        item: ScoredCandidate,
        corpus: list["_Existing"],
        matrix: np.ndarray,
    ) -> str | None:
        """Delegates to the shared `find_duplicate` (below) — the same gate
        UC8's innovation commit uses, so a Worker-proposed invariant and a
        curator-extracted one dedup against the corpus identically."""
        return find_duplicate(
            vector,
            [p.model_dump() for p in item.candidate.condition],
            item.candidate.effect.model_dump(),
            corpus,
            matrix,
            self._dedup_threshold,
            label=(item.candidate.claim or "")[:60],
        )

    async def _existing_embeddings(self) -> tuple[list["_Existing"], np.ndarray]:
        return await load_invariant_corpus(self._db)

    async def _document_author(self, document_id: str) -> str | None:
        rows = await self._db.query("SELECT author FROM document WHERE id = :d", d=document_id)
        return str(rows[0]["author"]) if rows and rows[0]["author"] else None

    async def _author_band(self, author: str | None) -> tuple[float, float, float]:
        """(floor, initial_min, initial_max) for the tier — the seeded bands
        are authoritative, so a re-tune is a seed change, not a code change."""
        tier = author or "other"
        rows = await self._db.query(
            "SELECT floor_weight, initial_weight_min, initial_weight_max "
            "FROM invariant_author_config WHERE author = :a",
            a=tier,
        )
        if not rows:
            raise ValueError(f"no author band seeded for tier {tier!r}")
        row = rows[0]
        return (
            float(row["floor_weight"]),
            float(row["initial_weight_min"]),
            float(row["initial_weight_max"]),
        )

    async def _create_invariant(
        self,
        tx: InvestmentDB,
        item: ScoredCandidate,
        author: str | None,
        band: tuple[float, float, float],
        vector: np.ndarray,
    ) -> str:
        floor, low, high = band
        candidate = item.candidate
        # The model PROPOSES weight_initial; the tier band binds it. Same
        # shape as UC8: a proposal outside the mechanical bounds is corrected,
        # not rejected — the claim is the contribution, the number is a guess.
        weight_initial = min(max(candidate.weight_initial, low), high)
        return await tx.create_vertex(
            "invariant",
            {
                "title": candidate.claim,
                "description": candidate.description,
                "example": candidate.example,
                "source": "curator",
                "author": author,
                # ADR-006: a candidate enters PROPOSED and earns its verdict
                # from the 35y sweep. Nothing here grants integration.
                "status": "proposed",
                "tags": candidate.tags,
                "embedding": to_blob(vector),
                "condition": [p.model_dump() for p in candidate.condition],
                "effect": candidate.effect.model_dump(),
                "weight_initial": weight_initial,
                "floor_weight": floor,
                "trace": f"UC4 curator (score {item.interest_score:.1f})",
            },
        )

    async def _create_reference_note(
        self,
        tx: InvestmentDB,
        note: ReferenceNote,
        document_id: str,
        author: str | None,
        passage_ids: list[str],
    ) -> str:
        """Reference knowledge: an Invariant with EMPTY condition and NO effect.

        Not a new entity — DATA_MODELS.md is explicit that "a ponctual fact is
        NOT a new entity", and that an invariant with empty condition/no effect
        IS reference knowledge: never confronted, market_score stays 1.0,
        weight = authority x recency. It informs Worker reasoning without
        backing a strategy.

        OPEN QUESTION for the owner (flagged, not silently decided): status.
        These enter 'proposed' like everything else, but they can never reach
        'integrated' by measurement — there is no condition to confront — so
        ADR-006's "nothing stays proposed forever" has no mechanism here.
        'proposed' is the recoverable choice: wrong status is fixable, and
        these notes were LOST entirely in the 2026-07-21 run."""
        floor, low, _ = await self._author_band(author)
        description = f"{note.description}\n\nNot reducible: {note.why_not_reducible}"
        note_id = await tx.create_vertex(
            "invariant",
            {
                "title": note.claim,
                "description": description,
                "source": "curator",
                "author": author,
                "status": "proposed",
                "tags": ["reference-knowledge"],
                # Embedded like any other invariant, and for the reason the
                # spec gives it a home at all: reference knowledge "informs
                # Worker reasoning". Unembedded, it would be unreachable by
                # the Planner's semantic search — persisted but inert, which
                # is barely better than the run that lost it.
                "embedding": to_blob(self._encode(note.claim, description)),
                "condition": [],
                "effect": None,
                "weight_initial": low,
                "floor_weight": floor,
                "trace": f"UC4 curator reference note from {document_id}",
            },
        )
        for passage_id in _cited(note.supporting_passages, passage_ids):
            await tx.create_edge("supports", passage_id, note_id, {"excerpt": None})
        return note_id

    async def _attach_evidence(
        self,
        tx: InvestmentDB,
        invariant_id: str,
        item: ScoredCandidate,
        passage_ids: list[str],
    ) -> None:
        """SUPPORTS edges from the passages that justify the claim.

        Idempotent by composite PK, so a merge into an invariant that already
        cites the passage is a no-op rather than a duplicate."""
        for passage_id in _cited(item.candidate.supporting_passages, passage_ids):
            await tx.create_edge(
                "supports",
                passage_id,
                invariant_id,
                {"strength": item.interest_score / 100.0, "excerpt": None},
            )


@dataclass(frozen=True)
class _Existing:
    """An invariant already in the corpus, reduced to what the dedup gate
    needs: its identity and its machine-readable structure."""

    id: str
    condition: list[dict[str, Any]]
    effect: dict[str, Any] | None


def _predicate_key(predicate: dict[str, Any]) -> tuple[str, str, str, float]:
    return (
        str(predicate.get("signal")),
        str(predicate.get("feature")),
        str(predicate.get("op")),
        float(predicate.get("value", 0.0)),
    )


def _identical_structure(
    condition: list[dict[str, Any]], effect: dict[str, Any], existing: _Existing
) -> bool:
    """Same predicates and same effect — order-insensitive, since a condition
    is an AND and `[A, B]` is `[B, A]`."""
    if existing.effect is None:
        return False
    if {k: effect.get(k) for k in _EFFECT_FIELDS} != {
        k: existing.effect.get(k) for k in _EFFECT_FIELDS
    }:
        return False
    return sorted(map(_predicate_key, condition)) == sorted(map(_predicate_key, existing.condition))


def _same_invariant(
    condition: list[dict[str, Any]], effect: dict[str, Any], existing: _Existing
) -> bool:
    """Do these two claims assert the SAME thing?

    Structure, not prose — the only part of a candidate that cannot be
    paraphrased. Reference knowledge (no effect) is never a merge target: it
    carries no condition to compare, so "similar wording" is all that would
    be left, which is exactly what this guard exists to distrust."""
    if existing.effect is None:
        return False
    same_effect = all(effect.get(field) == existing.effect.get(field) for field in _EFFECT_FIELDS)
    # `conditions_can_overlap` is the same helper the contradiction detector
    # uses: provably-disjoint predicates on the same (signal, feature) — wide
    # vs tight spreads — mean the two can never be active together, so they
    # cannot be one invariant.
    return same_effect and conditions_can_overlap(condition, existing.condition)


async def load_invariant_corpus(db: InvestmentDB) -> tuple[list["_Existing"], np.ndarray]:
    """Every embedded invariant reduced to what the dedup gate needs — its id +
    machine-readable structure — plus the stacked embedding matrix. Shared by
    the curator's writeback and UC8's innovation commit, so both dedup against
    the same corpus. Empty corpus -> a `(0, 0)` matrix (`find_duplicate`
    short-circuits on `matrix.size == 0`, so the exact width is immaterial)."""
    rows = await db.query(
        "SELECT id, embedding, condition, effect FROM invariant WHERE embedding IS NOT NULL"
    )
    if not rows:
        return [], np.empty((0, 0))
    corpus = [
        _Existing(
            id=str(row["id"]),
            condition=json.loads(row["condition"] or "[]"),
            effect=json.loads(row["effect"]) if row["effect"] else None,
        )
        for row in rows
    ]
    matrix = np.vstack([from_blob(row["embedding"]) for row in rows])
    return corpus, matrix


def find_duplicate(
    vector: np.ndarray,
    condition: list[dict[str, Any]],
    effect: dict[str, Any] | None,
    corpus: list["_Existing"],
    matrix: np.ndarray,
    threshold: float,
    *,
    label: str = "",
) -> str | None:
    """The id of an existing invariant this one RESTATES, or None — the shared
    dedup gate (docs/TASKS.md Phase 6 "DEDUP GATE"). TWO conditions, both
    required: prose similarity proposes, structure disposes. Ranked by
    similarity so the closest structurally-compatible match wins.

    `effect is None` (a reference note) is never a duplicate — it carries no
    structure to compare, and merging on wording alone is exactly what this
    gate distrusts."""
    if not corpus or matrix.size == 0 or effect is None:
        return None
    similarities = cosine_matrix(vector.reshape(1, -1), matrix)[0]

    # FIRST: exact structural identity, with NO cosine gate. Same predicates +
    # same effect produce byte-identical 35y confrontations — they ARE one
    # invariant whatever the prose says (measured 2026-07-21: two phrasings of
    # `equity_trend.level < 0` sat at cosine 0.668 and were persisted twice).
    for index, existing in enumerate(corpus):
        if _identical_structure(condition, effect, existing):
            logger.info(
                "dedup: merged into %s (identical structure, cosine %.3f)",
                existing.id,
                float(similarities[index]),
            )
            return existing.id
    for index in np.argsort(similarities)[::-1]:
        score = float(similarities[index])
        if score < threshold:
            return None
        existing = corpus[int(index)]
        if not _same_invariant(condition, effect, existing):
            # The measured trap: "wide spreads -> equities underperform" and
            # "tight spreads -> equities outperform" sit at cosine 0.907.
            # Disjoint conditions (or inverted direction) => two claims.
            logger.info(
                "dedup: kept %r apart from %s despite cosine %.3f (structure differs)",
                label,
                existing.id,
                score,
            )
            continue
        logger.info("dedup: merged into %s (cosine %.3f)", existing.id, score)
        return existing.id
    return None


def _cited(claimed: list[str], batch: list[str]) -> list[str]:
    """The passages the model cited, restricted to the ones actually in this
    batch — a model that invents or misremembers a passage id must not create
    a dangling SUPPORTS edge. Falls back to the whole batch when it cited
    nothing usable, which is honest: the claim came from these passages."""
    valid = [p for p in claimed if p in set(batch)]
    return valid or batch
