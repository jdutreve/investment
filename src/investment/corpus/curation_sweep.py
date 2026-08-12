"""UC4's weekly sweep — curate whatever the corpus has not curated yet
(docs/USE_CASES.md UC4 trigger 2: "Weekly cron (…, after UC3): sweep over
anything not yet curated"; CLAUDE.md "Scheduling", 08:10).

ONE PASS, TWO CALLERS. This is the loop `seed._seed_curation` has run since M7
— every document, one call each, one bad document never costing the others —
lifted out the moment the weekly chain became its second caller. A private
helper with two callers is a contract (CLAUDE.md: "WHEN A SECOND ONE ARRIVES"),
and the two must not drift: the seed's initial pass and the weekly sweep differ
only in when they run, never in what they do.

RE-RUNNING IS FREE, which is the property that makes a weekly sweep sane at all.
`curate_document` asks the checkpoint (`curated_passage`) which passages this
FINGERPRINT — prompt version + model + reasoning effort — has already seen, and
returns without an LLM call when the answer is "all of them". So on a stable
corpus the weekly sweep costs one query per document and nothing else; it earns
its place on exactly two occasions:

  - an ingestion whose curation FAILED (the watcher quarantines the file or the
    curator's batch raised) — those passages stay unmarked and the next sweep
    retries precisely them, which is what "resumable" means here;
  - a bumped `CURATION_PROMPT_VERSION`, which changes the fingerprint and asks
    the whole corpus to be read again with the new instructions. That is a
    deliberate act, and the sweep is what carries it out over the following
    Monday rather than in one interactive sitting.

WHAT IT DOES NOT DO: re-curate on its own initiative. UC4's trigger-2 sentence
also mentions "re-curation opportunities on existing invariants", and there is
no mechanism for that beyond the fingerprint — an invariant's standing is moved
by CONFRONTATION (ADR-006's maturation), not by asking the curator again. Left
unbuilt rather than approximated.
"""

import dataclasses
import logging

from investment.config import Settings
from investment.corpus.embedding import InProcessEmbedder
from investment.db.sqlite import InvestmentDB
from investment.worker.curator import KnowledgeCurator
from investment.writeback.knowledge import KnowledgeWriteback

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CurationSweep:
    """What the sweep got through. `failed` lists document ids rather than
    counting them: a document that keeps failing every week is a fact about
    that document, and a number would never lead anyone to it."""

    documents: int
    candidates: int
    failed: list[str]


async def sweep_corpus(
    db: InvestmentDB,
    settings: Settings,
    *,
    embedder: InProcessEmbedder | None = None,
) -> CurationSweep:
    """Curate every document that has passages this fingerprint has not seen.

    `embedder` is injectable because loading the model costs seconds and
    hundreds of megabytes: the running agent already holds one (main.py) and
    must not build a second, while the seed has no reason to care.

    A FAILING DOCUMENT DOES NOT ABORT THE SWEEP, and total failure DOES raise.
    The per-document `except` exists so one bad response cannot cost the other
    documents their pass; if nothing at all got through, that same `except`
    would turn a total outage into a tidy inventory line, so the caller is told
    — loudly enough for the chain to abort and alert (main.py)."""
    embedder = embedder or InProcessEmbedder(settings.embedding_model)
    writeback = KnowledgeWriteback(db, embedder)
    curator = KnowledgeCurator(
        db,
        model_name=settings.planner_model,
        api_key=settings.openrouter_api_key,
        reasoning_effort=settings.curator_reasoning_effort,
    )

    documents = await db.query("SELECT id, title FROM document ORDER BY id")
    curated = candidates = 0
    failed: list[str] = []
    for row in documents:
        document_id = str(row["id"])
        try:
            scored = await curator.curate_document(document_id, writeback)
        except Exception as exc:  # one bad document must not cost the others
            logger.warning("curation sweep: %s FAILED — %s: %s", row["title"], type(exc), exc)
            failed.append(document_id)
            continue
        curated += 1
        candidates += len(scored)

    if failed and curated == 0:
        raise RuntimeError(
            f"curation sweep: every document failed curation ({len(failed)}) — "
            "see the warnings above; nothing was persisted"
        )
    sweep = CurationSweep(documents=curated, candidates=candidates, failed=failed)
    logger.info("curation sweep: %s", sweep)
    return sweep
