"""Group innovations into THEMES, so a critique that keeps coming back says so.

WHY THIS EXISTS. Over the M8b runs of 2026-08-08/09, every finding that turned
out to be real was proposed more than once from independent dates — the
credit-spread velocity critique six ways, the concentration cap freezing the
stack twice, the regime label's divergence twice — and the two that were
measured and rejected (the gold haven, the shorter hysteresis) were repeats too,
so recurrence is not a proof. What it is, reliably, is where to LOOK: it ranked
the six-way critique above the one-off, and that critique turned out to be the
screen's one robust finding.

Nothing counted them. Invariants are deduplicated (`knowledge.find_duplicate`),
innovations were not, so the signal sat in the output and nowhere in the system.
The owner's objection to a note that said "revisit when someone notices" was the
right one: nothing was watching, which means nobody was.

DISTINCT TITLES, NOT ROWS. Two runs replaying the same date produce the same
sentence twice — one observation, repeated. Six different wordings of "read the
spread's TRAJECTORY, not its level" are six independent arrivals at one idea.
Only the second is evidence, so `theme_recurrence` counts distinct titles.

THE SAME EMBEDDER AND THE SAME GEOMETRY as the invariant dedup, deliberately:
one notion of "these two claims are the same claim" in the codebase, not two
that can drift apart.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from ulid import ULID

from investment.corpus.embedding import Embedder, cosine_matrix, from_blob, to_blob
from investment.db.sqlite import InvestmentDB
from investment.worker.result import ImprovementProposal

logger = logging.getLogger(__name__)

# CALIBRATED ON THE REAL CORPUS, and the first attempt was wrong.
#
# 0.82 was picked by reasoning — innovations are prose, with no structure to
# corroborate a match the way `knowledge._same_invariant` has, so surely the bar
# should be higher than the corpus dedup's 0.75. Measured against the 25 actual
# M8b innovations, that reasoning failed: at 0.82 only two themes group at all,
# and the six-wording velocity critique — the case the whole ledger exists for —
# splits into singletons.
#
# The distributions do not separate. Over the real corpus, same-theme pairs run
# 0.585 to 0.837 and different-theme pairs reach 0.832, so NO pairwise threshold
# is clean; the best pair of two different critiques scores above the median
# pair of one. That is the same shape as the ranking rule's non-transitive tie
# (CLAUDE.md), and it means the choice is which error to make.
#
# 0.75 — the corpus dedup's own value, so there is ONE notion of "the same
# claim" in the codebase rather than two that drift. Measured, it reproduces the
# human reading of the corpus: 16 themes, five of them plural, grouping the
# velocity critique (4 of 6 wordings), the haven chain, the cap exemption, and
# the two regime-label complaints that became I-53.
#
# IT OVER-MERGES AT THE MARGIN, and that is handled by DISPLAY rather than by
# tuning: two of the velocity wordings land in a neighbouring "the wide book
# takes risk while credit is stressed" group. A silent false merge would hide
# one critique inside another forever, so the digest prints the theme's distinct
# wordings, not just its count — a bad grouping is then visible and annoying
# instead of invisible and lossy.
THEME_COSINE_THRESHOLD = 0.75

# Distinct wordings before a theme is called out. Not a statistical bar — with
# counts this small nothing would be — but the point at which "the same idea
# keeps arriving from different angles" stops being plausibly one accident.
# Below it the count is still recorded and still queryable; this only decides
# when the system speaks up on its own.
NOTABLE_RECURRENCE = 3


def innovation_embedding_input(proposal: ImprovementProposal) -> str:
    """Title and rationale, in that order — the same shape
    `invariant_embedding_input` uses, so the two corpora are comparable."""
    return f"{proposal.title}\n{proposal.rationale}"


async def _themes(db: InvestmentDB) -> tuple[list[str], np.ndarray]:
    """Every innovation's theme id and embedding, as a matrix to match against."""
    rows = await db.query(
        "SELECT theme_id, embedding FROM innovation WHERE embedding IS NOT NULL ORDER BY created_at"
    )
    if not rows:
        return [], np.empty((0, 0))
    return (
        [str(r["theme_id"]) for r in rows],
        np.vstack([from_blob(bytes(r["embedding"])) for r in rows]),
    )


async def theme_recurrence(db: InvestmentDB, theme_id: str) -> int:
    """How many DISTINCT wordings this theme has arrived in. See the module
    docstring for why distinct titles rather than rows."""
    rows = await db.query(
        "SELECT COUNT(DISTINCT title) AS n FROM innovation WHERE theme_id = :t", t=theme_id
    )
    return int(rows[0]["n"]) if rows else 0


async def record_innovation(
    db: InvestmentDB,
    proposal: ImprovementProposal,
    embedder: Embedder,
    *,
    today: Any,
    source_id: str | None = None,
) -> tuple[str, int]:
    """File one innovation under its theme. Returns `(theme_id, recurrence)`.

    Writes in its OWN transaction rather than joining the caller's: this is a
    ledger of what was proposed, and it must survive a commit that fails on the
    innovation's own merits — a `strategy_revision` whose spec cannot build a
    candidate portfolio still happened, and its recurrence still counts. That is
    the same reasoning `journal_worker_reading` follows for the reading."""
    vector = embedder.encode([innovation_embedding_input(proposal)])[0]
    theme_ids, matrix = await _themes(db)

    theme_id = f"thm-{ULID()}"
    if matrix.size:
        similarities = cosine_matrix(vector.reshape(1, -1), matrix)[0]
        best = int(np.argmax(similarities))
        if float(similarities[best]) >= THEME_COSINE_THRESHOLD:
            theme_id = theme_ids[best]

    now = datetime.now(UTC).isoformat()
    async with db.transaction() as tx:
        await tx.command(
            "INSERT INTO innovation (id, type, title, rationale, spec, theme_id, source_id, "
            "embedding, date, trace, created_at) VALUES (:id, :type, :title, :rationale, :spec, "
            ":theme, :source, :emb, :date, :trace, :now)",
            id=f"inn-{ULID()}",
            type=str(proposal.type),
            title=proposal.title,
            rationale=proposal.rationale,
            spec=json.dumps(proposal.spec, default=str),
            theme=theme_id,
            source=source_id,
            emb=to_blob(vector),
            date=str(today),
            trace=proposal.trace or "UC8 agent-discovery innovation",
            now=now,
        )

    recurrence = await theme_recurrence(db, theme_id)
    if recurrence >= NOTABLE_RECURRENCE:
        # THE POINT OF THE WHOLE MODULE. A critique arriving in a third distinct
        # wording has stopped being an accident of one cycle, and the system now
        # says so by itself instead of waiting for someone to notice.
        logger.warning(
            "RECURRING INNOVATION (%d distinct wordings, theme %s): %r — worth measuring",
            recurrence,
            theme_id,
            proposal.title,
        )
    return theme_id, recurrence


async def import_run_innovations(
    db: InvestmentDB, journals: Path, embedder: Embedder
) -> tuple[int, int]:
    """File an archived agentic-replay run's innovations into the ledger.
    Returns `(imported, skipped)`.

    NOT A ONE-OFF MIGRATION, which is why it lives here rather than in a
    scratchpad. The agentic replay runs every date against a THROWAWAY SNAPSHOT
    — that isolation is the whole point of `db/as_of_snapshot.py` — so
    everything the Worker proposes is committed to a database that is then
    deleted. Verified on 2026-08-11: after two full runs and 25 distinct
    innovations, the live database held zero of them, and the recurrence ledger
    would have started blind on a corpus that was sitting on disk.

    So this is the ONLY path from a run to the ledger, and it will be needed
    again after the next one.

    IDEMPOTENT on `(title, date)`: a re-import adds nothing, and re-running it
    over a directory that grew by one episode costs one embed per new
    innovation. Recurrence counts distinct titles anyway, so a duplicated row
    could not inflate a count — the skip is about not paying twice, and about a
    ledger that reads as a record rather than as an accumulation of re-runs."""
    proposals: list[tuple[str, ImprovementProposal]] = []
    for path in sorted(journals.rglob("*.dates.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # The same tolerance `_read_journal` applies: a run killed
                # mid-write leaves one torn line, and it must not cost the file.
                logger.warning("skipping an unreadable journal line in %s", path)
                continue
            for raw in row.get("innovation_proposals") or []:
                proposals.append((str(row.get("as_of", "")), ImprovementProposal(**raw)))

    imported = skipped = 0
    for as_of, proposal in proposals:
        existing = await db.query(
            "SELECT 1 FROM innovation WHERE title = :t AND date = :d LIMIT 1",
            t=proposal.title,
            d=as_of,
        )
        if existing:
            skipped += 1
            continue
        await record_innovation(db, proposal, embedder, today=as_of)
        imported += 1
    logger.info("imported %d innovations from %s (%d already present)", imported, journals, skipped)
    return imported, skipped
