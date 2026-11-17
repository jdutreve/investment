"""The literature, admitted automatically (ADR-006 amendment 2026-11-15, owner 2026-11-17).

A claim about what markets do over more than three years is one the engine cannot
measure: a 10-year claim gets about five independent moments in 58 years, and the
first checkpoint needs ten. What it can do is read who said it. Two steps, both on
the curator's model, both automatic — nobody files or approves anything (owner,
2026-11-17: "tout doit être automatique"):

1. READ. Every authored reference note is read once: is it a long-horizon market
   claim, and if so, in what canonical wording (`literature_reading`).
2. FACTOR. The long-horizon claims are grouped by idea, the existing statements
   among them. A group becomes ONE statement of its idea and its notes are
   factored into it (`invariant.factored_into`), so the idea is read once:
   - stated by two or more AUTHORS, the statement is `literature` — settled by the
     literature, never weighed or judged by the engine;
   - stated by one author in several notes, it is a reference note like the ones
     it replaces, and is promoted the day a second author joins it.

Grouping is the model's, not a cosine's: on the 2026-11-17 ice core, MiniLM put no
two authors' versions of one idea above 0.70, while the model grouped them right.

The Worker reads a statement when the week's retrieval finds it relevant, like any
other invariant (owner, 2026-11-17: "en rapport du contexte courant").
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models.openai import OpenAIChatModelSettings
from ulid import ULID

from investment.config import Settings
from investment.corpus.embedding import Embedder, invariant_embedding_input, to_blob
from investment.db.sqlite import InvestmentDB
from investment.mechanical.invariants import LITERATURE_STATUS, REFERENCE_STATUS
from investment.openrouter_client import build_native_output_model

logger = logging.getLogger(__name__)

LITERATURE_EVENT = "LiteratureClaimEvent"
FACTORED_SOURCE = "factored"
MIN_AUTHORS = 2
READ_BATCH = 20
MAX_CONCURRENT_READS = 4
TIMEOUT_SECONDS = 600.0
NOTE_CHARS = 600


class NoteReading(BaseModel):
    id: str
    long_horizon_claim: bool
    horizon_years: int | None = None
    canonical: str | None = None


class Readings(BaseModel):
    notes: list[NoteReading]


class IdeaGroup(BaseModel):
    ids: list[str]
    statement: str


class Grouping(BaseModel):
    groups: list[IdeaGroup]


READ_INSTRUCTIONS = """You read notes extracted from investment books. For EACH note, decide whether
it is a CLAIM ABOUT MARKETS WHOSE HORIZON IS LONGER THAN THREE YEARS: what returns, prices or risks
will be over the following several years, a decade or more (valuation and long-run returns, secular
trends, long-run premia, debt-cycle phases lasting years). Mechanisms, definitions, history, advice,
behaviour, short-horizon trading claims and one-off events are NOT.
For a long-horizon claim, write `canonical`: ONE plain-English sentence, condition then consequence
then horizon, in general terms (no names, dates, countries or numbers from one episode), worded so
that the same idea stated by two different authors reads the same. Give `horizon_years` (3, 5, 10,
20). Return one entry per note, with its id."""

GROUP_INSTRUCTIONS = """You receive long-horizon market claims extracted from investment books, one
per line as [id] (author) claim, and the STATEMENTS already written for some ideas, as
[id] (STATEMENT) text. Group the claims that state THE SAME IDEA: the same condition leading to the
same consequence, even if worded differently or more or less specific. Claims that merely share a
topic but say different things are NOT the same idea. A claim that states an existing STATEMENT's
idea joins its group, with the statement's id among the group's ids.
For each group, write `statement`: one plain-English sentence that states the idea as all its
members support it, no stronger than the weakest of them. Return only groups of two or more ids;
every id at most once."""


@dataclass(frozen=True)
class LiteratureReport:
    """What one pass read and factored, for the sweep's log line."""

    notes_read: int
    statements_created: int
    notes_factored: int
    admitted: int  # statements that are `literature` after the pass, new or promoted


def _agent(settings: Settings, output: type[BaseModel], instructions: str) -> Agent[None, Any]:
    return Agent(
        build_native_output_model(
            settings.curator_model, settings.openrouter_api_key, read_timeout=TIMEOUT_SECONDS
        ),
        output_type=NativeOutput(output),
        instructions=instructions,
        retries=2,
        model_settings=OpenAIChatModelSettings(
            timeout=TIMEOUT_SECONDS,
            openai_reasoning_effort=settings.curator_reasoning_effort,  # type: ignore[typeddict-item]
        ),
    )


async def _note_sources(db: InvestmentDB, note_ids: list[str]) -> dict[str, tuple[str, str]]:
    """`note id -> (author, book title)`, from the passage the curator cited it from."""
    sources: dict[str, tuple[str, str]] = {}
    for note_id in note_ids:
        rows = await db.query(
            "SELECT d.author, d.title FROM supports su JOIN passage p ON p.id = su.passage_id "
            "JOIN document d ON d.id = p.document_id WHERE su.invariant_id = :i AND su.cited = 1 "
            "AND d.author IS NOT NULL AND d.author <> '' LIMIT 1",
            i=note_id,
        )
        if rows:
            sources[note_id] = (str(rows[0]["author"]), str(rows[0]["title"]))
    return sources


async def read_notes(db: InvestmentDB, reader: Agent[None, Readings]) -> int:
    """Read every authored reference note not read yet; one row per note, written
    per batch, so a pass that stops resumes where it stopped."""
    notes = await db.query(
        "SELECT DISTINCT i.id, i.title, i.description FROM invariant i "
        "JOIN supports su ON su.invariant_id = i.id AND su.cited = 1 "
        "JOIN passage p ON p.id = su.passage_id JOIN document d ON d.id = p.document_id "
        "WHERE i.status = :reference AND i.source <> :factored AND d.author IS NOT NULL "
        "AND d.author <> '' AND d.kind <> 'event' "
        "AND i.id NOT IN (SELECT invariant_id FROM literature_reading) ORDER BY i.id",
        reference=REFERENCE_STATUS,
        factored=FACTORED_SOURCE,
    )
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_READS)
    read = 0

    async def one(batch: list[dict[str, Any]]) -> None:
        nonlocal read
        prompt = "\n\n".join(
            f"[{n['id']}] {n['title']} — {str(n['description'] or '')[:NOTE_CHARS]}" for n in batch
        )
        async with semaphore:
            try:
                readings = (await reader.run(prompt)).output.notes
            except Exception as exc:  # one batch must not cost the others; retried next pass
                logger.warning("literature: a batch of %d notes failed: %s", len(batch), exc)
                return
        asked = {str(n["id"]) for n in batch}
        now = datetime.now(UTC).isoformat()
        async with db.transaction() as tx:
            for r in readings:
                if r.id not in asked:
                    continue
                await tx.command(
                    "INSERT OR REPLACE INTO literature_reading (invariant_id, long_horizon, "
                    "horizon_years, canonical, read_at) VALUES (:i, :l, :h, :c, :t)",
                    i=r.id,
                    l=int(r.long_horizon_claim),
                    h=r.horizon_years,
                    c=r.canonical if r.long_horizon_claim else None,
                    t=now,
                )
                read += 1

    rows = [dict(n) for n in notes]
    await asyncio.gather(*(one(rows[i : i + READ_BATCH]) for i in range(0, len(rows), READ_BATCH)))
    return read


async def factor_ideas(
    db: InvestmentDB, grouper: Agent[None, Grouping], embedder: Embedder, today: date
) -> tuple[int, int, int]:
    """Group the unfactored long-horizon claims with the existing statements, and
    write each group as one statement. Returns `(created, factored, admitted)`."""
    pending = await db.query(
        "SELECT r.invariant_id AS id, r.canonical FROM literature_reading r "
        "JOIN invariant i ON i.id = r.invariant_id "
        "WHERE r.long_horizon = 1 AND r.canonical IS NOT NULL AND i.factored_into IS NULL"
    )
    if not pending:
        return 0, 0, 0
    statements = await db.query(
        "SELECT id, title, status, author FROM invariant WHERE source = :factored",
        factored=FACTORED_SOURCE,
    )
    sources = await _note_sources(db, [str(p["id"]) for p in pending])
    canonical = {str(p["id"]): str(p["canonical"]) for p in pending if str(p["id"]) in sources}
    by_id = {str(s["id"]): s for s in statements}
    prompt = "\n".join(
        [f"[{s['id']}] (STATEMENT) {s['title']}" for s in statements]
        + [f"[{i}] ({sources[i][0]}) {text}" for i, text in canonical.items()]
    )
    grouping = (await grouper.run(prompt)).output

    created = factored = admitted = 0
    for group in grouping.groups:
        notes = [i for i in dict.fromkeys(group.ids) if i in canonical]
        anchors = [i for i in group.ids if i in by_id]
        if not notes or (not anchors and len(notes) < 2):
            continue
        if anchors:
            statement_id = anchors[0]
        else:
            statement_id = f"inv-idea-{ULID()}"
            created += 1
        members = await _members(db, statement_id) + notes
        member_sources = await _note_sources(db, members)
        authors = sorted({a for a, _ in member_sources.values()})
        books = sorted({b for _, b in member_sources.values()})
        status = LITERATURE_STATUS if len(authors) >= MIN_AUTHORS else REFERENCE_STATUS
        promoted = status == LITERATURE_STATUS and (
            not anchors or by_id[statement_id]["status"] != LITERATURE_STATUS
        )
        title = str(by_id[statement_id]["title"]) if anchors else group.statement
        await _write_statement(
            db,
            embedder,
            statement_id,
            title,
            authors,
            books,
            notes,
            status,
            today,
            new=not anchors,
            promoted=promoted,
        )
        factored += len(notes)
        admitted += int(promoted)
    return created, factored, admitted


async def _members(db: InvestmentDB, statement_id: str) -> list[str]:
    rows = await db.query("SELECT id FROM invariant WHERE factored_into = :s", s=statement_id)
    return [str(r["id"]) for r in rows]


async def _write_statement(
    db: InvestmentDB,
    embedder: Embedder,
    statement_id: str,
    title: str,
    authors: list[str],
    books: list[str],
    notes: list[str],
    status: str,
    today: date,
    *,
    new: bool,
    promoted: bool,
) -> None:
    description = f"{title} Stated by {', '.join(authors)}."
    example = f"Sources: {'; '.join(books)}"
    async with db.transaction() as tx:
        if promoted or new:
            await tx.append_event(
                type=LITERATURE_EVENT,
                source_uc="curation",
                source_id=statement_id,
                payload={
                    "statement": title,
                    "status": status,
                    "authors": authors,
                    "factored": notes,
                    "promoted": promoted and not new,
                },
                event_date=today,
            )
        if new:
            vector = embedder.encode([invariant_embedding_input(title, description)])[0]
            await tx.create_vertex(
                "invariant",
                {
                    "id": statement_id,
                    "title": title,
                    "description": description,
                    "example": example,
                    "source": FACTORED_SOURCE,
                    "author": ", ".join(authors),
                    "status": status,
                    "tags": ["literature" if status == LITERATURE_STATUS else "idea"],
                    "embedding": to_blob(vector),
                    "condition": [],
                    "effect": None,
                    # NOT NULL columns, meaningless here: nothing measured, no weight.
                    "weight_initial": 0.0,
                    "floor_weight": 0.0,
                    "weight_effective": None,
                    "trace": f"Factored from {len(notes)} note(s) of {', '.join(authors)}.",
                },
            )
        else:
            await tx.command(
                "UPDATE invariant SET status = :s, author = :a, description = :d, example = :e, "
                "tags = :t, updated_at = :now WHERE id = :i",
                s=status,
                a=", ".join(authors),
                d=description,
                e=example,
                t='["literature"]' if status == LITERATURE_STATUS else '["idea"]',
                now=datetime.now(UTC).isoformat(),
                i=statement_id,
            )
        for note in notes:
            await tx.command(
                "UPDATE invariant SET factored_into = :s WHERE id = :n", s=statement_id, n=note
            )


async def admit_literature(
    db: InvestmentDB, settings: Settings, embedder: Embedder, today: date
) -> LiteratureReport:
    """Read the new notes, then factor what they add. Called at the end of every
    curation sweep (corpus/curation_sweep.py)."""
    read = await read_notes(db, _agent(settings, Readings, READ_INSTRUCTIONS))
    created, factored, admitted = await factor_ideas(
        db, _agent(settings, Grouping, GROUP_INSTRUCTIONS), embedder, today
    )
    report = LiteratureReport(read, created, factored, admitted)
    logger.info("literature: %s", report)
    return report
