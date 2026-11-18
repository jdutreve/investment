"""The literature, admitted automatically (writeback/literature.py): notes read once,
the same idea factored into one statement, `literature` when two authors state it,
never measured, and read only when the week's retrieval finds it relevant."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical import invariants
from investment.planner.retrieval import _fetch_invariants
from investment.writeback import literature as L

TODAY = date(2026, 11, 17)


class _Embedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 384), dtype=np.float32)


@dataclass
class _Result:
    output: Any


class _Model:
    """Stands in for an agent: answers each `run` with the next prepared output."""

    def __init__(self, *outputs: Any) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> _Result:
        self.prompts.append(prompt)
        return _Result(self.outputs.pop(0))


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "literature.db")
    yield conn
    await conn.close()


async def _note(db: InvestmentDB, note_id: str, author: str, book: str) -> None:
    """A reference note cited from a passage of `book` by `author`."""
    doc = f"doc-{book}"
    await db.command(
        "INSERT OR IGNORE INTO document (id, title, author, kind, source_type, ingested_at, "
        "trace) VALUES (:d, :b, :a, 'book', 'pdf', '2026-01-01', 't')",
        d=doc,
        b=book,
        a=author,
    )
    await db.command(
        "INSERT INTO passage (id, document_id, position, content, created_at) "
        "VALUES (:p, :d, 0, 'text', '2026-01-01')",
        p=f"p-{note_id}",
        d=doc,
    )
    await db.command(
        "INSERT INTO invariant (id, title, description, source, status, condition, "
        "weight_initial, floor_weight, trace, created_at, updated_at) VALUES (:i, :t, 'd', "
        "'curator', 'reference', '[]', 0.5, 0.05, 't', '2026-01-01', '2026-01-01')",
        i=note_id,
        t=f"note {note_id}",
    )
    await db.command(
        "INSERT INTO supports (passage_id, invariant_id, cited) VALUES (:p, :i, 1)",
        p=f"p-{note_id}",
        i=note_id,
    )


def _readings(*ids: str) -> L.Readings:
    return L.Readings(
        notes=[
            L.NoteReading(id=i, long_horizon_claim=True, horizon_years=10, canonical=f"claim {i}")
            for i in ids
        ]
    )


async def test_two_authors_make_one_literature_statement(db: InvestmentDB) -> None:
    await _note(db, "n1", "Dimson", "Triumph")
    await _note(db, "n2", "Faber", "Asset Allocation")
    assert await L.read_notes(db, _Model(_readings("n1", "n2"))) == 2  # type: ignore[arg-type]
    idea = "Expensive equities return less over the next decade."
    grouper = _Model(L.Grouping(groups=[L.IdeaGroup(ids=["n1", "n2"], statement=idea)]))
    created, factored, admitted = await L.factor_ideas(db, grouper, _Embedder(), TODAY)  # type: ignore[arg-type]
    assert (created, factored, admitted) == (1, 2, 1)
    statement = (await db.query("SELECT * FROM invariant WHERE source = 'factored'"))[0]
    assert statement["status"] == invariants.LITERATURE_STATUS
    assert statement["weight_effective"] is None
    assert statement["title"] == idea
    assert "Dimson" in statement["author"] and "Faber" in statement["author"]
    notes = await db.query("SELECT factored_into FROM invariant WHERE id IN ('n1', 'n2')")
    assert {n["factored_into"] for n in notes} == {statement["id"]}
    events = await db.query("SELECT source_id FROM event_log WHERE type = :t", t=L.LITERATURE_EVENT)
    assert [e["source_id"] for e in events] == [statement["id"]]


async def test_one_author_is_factored_but_not_admitted_until_a_second_joins(
    db: InvestmentDB,
) -> None:
    await _note(db, "n1", "Dimson", "Triumph")
    await _note(db, "n2", "Dimson", "Triumph")
    await L.read_notes(db, _Model(_readings("n1", "n2")))  # type: ignore[arg-type]
    grouper = _Model(L.Grouping(groups=[L.IdeaGroup(ids=["n1", "n2"], statement="idea")]))
    await L.factor_ideas(db, grouper, _Embedder(), TODAY)  # type: ignore[arg-type]
    statement = (await db.query("SELECT id, status FROM invariant WHERE source = 'factored'"))[0]
    assert statement["status"] == invariants.REFERENCE_STATUS

    await _note(db, "n3", "Faber", "Asset Allocation")
    await L.read_notes(db, _Model(_readings("n3")))  # type: ignore[arg-type]
    joins = _Model(L.Grouping(groups=[L.IdeaGroup(ids=[statement["id"], "n3"], statement="x")]))
    created, factored, admitted = await L.factor_ideas(db, joins, _Embedder(), TODAY)  # type: ignore[arg-type]
    assert (created, factored, admitted) == (0, 1, 1)
    after = (
        await db.query("SELECT status, title FROM invariant WHERE id = :i", i=statement["id"])
    )[0]
    assert after["status"] == invariants.LITERATURE_STATUS
    assert after["title"] == "idea"  # an existing statement keeps its wording
    assert f"[{statement['id']}] (STATEMENT) idea" in joins.prompts[0]


async def test_nothing_new_to_factor_makes_no_model_call(db: InvestmentDB) -> None:
    grouper = _Model()
    assert await L.factor_ideas(db, grouper, _Embedder(), TODAY) == (0, 0, 0)  # type: ignore[arg-type]
    assert grouper.prompts == []


async def test_a_statement_is_never_measured_and_its_notes_are_read_through_it(
    db: InvestmentDB,
) -> None:
    await _note(db, "n1", "Dimson", "Triumph")
    await _note(db, "n2", "Faber", "Asset Allocation")
    await L.read_notes(db, _Model(_readings("n1", "n2")))  # type: ignore[arg-type]
    grouper = _Model(L.Grouping(groups=[L.IdeaGroup(ids=["n1", "n2"], statement="idea")]))
    await L.factor_ideas(db, grouper, _Embedder(), TODAY)  # type: ignore[arg-type]
    statement = (await db.query("SELECT * FROM invariant WHERE source = 'factored'"))[0]
    result = await invariants._mature_one(db, statement, None, True)  # type: ignore[arg-type]
    assert result.status == invariants.LITERATURE_STATUS
    assert await invariants.restate_invariant(db, statement["id"], {}, TODAY) == "literature"
    retrieved = await _fetch_invariants(db, ["n1", statement["id"], "n2"])
    assert [r["id"] for r in retrieved] == [statement["id"]]


async def test_two_statements_of_one_idea_merge_and_reach_two_authors(db: InvestmentDB) -> None:
    """The first live pass left Dimson's and Faber's "cash loses to inflation over a
    decade" as two one-author statements: an idea split in two never reaches two
    authors. Grouped together, they merge, and the merged one is literature."""
    for note, author, book in (
        ("d1", "Dimson", "Triumph"),
        ("d2", "Dimson", "Triumph"),
        ("f1", "Faber", "Asset Allocation"),
        ("f2", "Faber", "Asset Allocation"),
    ):
        await _note(db, note, author, book)
    await L.read_notes(db, _Model(_readings("d1", "d2", "f1", "f2")))  # type: ignore[arg-type]
    split = _Model(
        L.Grouping(
            groups=[
                L.IdeaGroup(ids=["d1", "d2"], statement="cash loses to inflation"),
                L.IdeaGroup(ids=["f1", "f2"], statement="bills earn nothing real"),
            ]
        )
    )
    await L.factor_ideas(db, split, _Embedder(), TODAY)  # type: ignore[arg-type]
    ids = [r["id"] for r in await db.query("SELECT id FROM invariant WHERE source = 'factored'")]
    assert len(ids) == 2

    merge = _Model(L.Grouping(groups=[L.IdeaGroup(ids=ids, statement="cash loses")]))
    created, _factored, admitted = await L.factor_ideas(db, merge, _Embedder(), TODAY)  # type: ignore[arg-type]
    assert (created, admitted) == (0, 1)
    live = await db.query(
        "SELECT id, status FROM invariant WHERE source = 'factored' AND factored_into IS NULL"
    )
    assert len(live) == 1 and live[0]["status"] == invariants.LITERATURE_STATUS
    notes = await db.query("SELECT factored_into FROM invariant WHERE id IN ('d1','d2','f1','f2')")
    assert {n["factored_into"] for n in notes} == {live[0]["id"]}
