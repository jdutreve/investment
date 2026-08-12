"""The Monday curation sweep (`corpus/curation_sweep.py`, docs/USE_CASES.md UC4
trigger 2).

The property worth a test is the one that makes a WEEKLY sweep sane: re-running
over a corpus this fingerprint has already read must cost nothing — not merely
be safe, but make no model call at all. That is asserted here without a network
by checkpointing every passage first: if the sweep called the model it would
401 against the stub key, exactly as `test_seed_corpus` shows it does when the
checkpoint is empty.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from investment.config import Settings
from investment.corpus.curation_sweep import sweep_corpus
from investment.db.sqlite import InvestmentDB
from investment.worker.curator import curation_fingerprint


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        openrouter_api_key="stub-key-that-401s",
        fred_api_key="x",
        planner_model="test/planner",
        worker_model="test/worker",
        telegram_bot_token="t",
        telegram_chat_id="c",
        db_path=tmp_path / "sweep.db",
        inbox_path=tmp_path / "inbox",
        sources_path=tmp_path / "sources",
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "sweep.db")
    yield conn
    await conn.close()


async def _document(db: InvestmentDB, document_id: str, passages: int) -> list[str]:
    await db.command(
        "INSERT INTO document (id, title, kind, author, source_type, ingested_at, trace) "
        "VALUES (:id, :id, 'book', 'Ray Dalio', 'pdf', '2026-08-01', 't')",
        id=document_id,
    )
    ids = []
    for position in range(passages):
        passage_id = f"{document_id}-p{position}"
        await db.command(
            "INSERT INTO passage (id, document_id, content, position, page, created_at) "
            "VALUES (:id, :doc, :content, :pos, 1, '2026-08-01')",
            id=passage_id,
            doc=document_id,
            content=f"passage {position} about credit spreads and drawdowns",
            pos=position,
        )
        ids.append(passage_id)
    return ids


async def _checkpoint(db: InvestmentDB, passage_ids: list[str], fingerprint: str) -> None:
    for passage_id in passage_ids:
        await db.command(
            "INSERT INTO curated_passage (passage_id, fingerprint, curated_at, candidate_count) "
            "VALUES (:p, :f, :now, 0)",
            p=passage_id,
            f=fingerprint,
            now=datetime.now(UTC).isoformat(),
        )


async def test_an_empty_corpus_sweeps_cleanly(db: InvestmentDB, tmp_path: Path) -> None:
    """A database with no documents is not a failure — it is a fresh install,
    and the escalation rule must not read it as "everything failed"."""
    sweep = await sweep_corpus(db, _settings(tmp_path))
    assert sweep == type(sweep)(documents=0, candidates=0, failed=[])


async def test_a_fully_checkpointed_corpus_costs_no_model_call(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """THE PROPERTY THE WEEKLY CADENCE RESTS ON. The stub key 401s on any model
    call, so a sweep that reached the network would fail this document and
    report it — reaching the checkpoint instead is what "re-running must be
    free" means (`curate_document`: "no call, no cost, no duplicate")."""
    settings = _settings(tmp_path)
    passages = await _document(db, "doc-1", passages=3)
    await _checkpoint(
        db,
        passages,
        curation_fingerprint(settings.planner_model, settings.curator_reasoning_effort),
    )

    sweep = await sweep_corpus(db, settings)

    assert sweep.failed == []
    assert sweep.documents == 1
    assert sweep.candidates == 0


async def test_a_bumped_fingerprint_makes_the_corpus_uncurated_again(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """The second occasion the sweep exists for. A checkpoint written under a
    DIFFERENT fingerprint — a bumped prompt version, a swapped model — does not
    answer for this one, so the document is read again. Here that means the
    model IS called, which against the stub key is a recorded failure: the
    assertion is that the sweep TRIED, not that it succeeded."""
    settings = _settings(tmp_path)
    passages = await _document(db, "doc-1", passages=2)
    await _checkpoint(db, passages, "v0/another-model/low")

    with pytest.raises(RuntimeError, match="every document failed curation"):
        await sweep_corpus(db, settings)


async def test_one_failing_document_does_not_cost_the_others(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """A long job must not lose the rest of the corpus to one bad response. The
    failed document is named — a count would never lead anyone to it — and its
    passages stay unmarked, so the next Monday retries precisely those."""
    settings = _settings(tmp_path)
    fingerprint = curation_fingerprint(settings.planner_model, settings.curator_reasoning_effort)
    await _checkpoint(db, await _document(db, "doc-ok", passages=2), fingerprint)
    await _document(db, "doc-bad", passages=2)  # uncurated -> reaches the stub key -> 401

    sweep = await sweep_corpus(db, settings)

    assert sweep.documents == 1  # the checkpointed one still passed
    assert sweep.failed == ["doc-bad"]
    rows = await db.query(
        "SELECT COUNT(*) AS n FROM curated_passage WHERE passage_id LIKE 'doc-bad%'"
    )
    assert int(rows[0]["n"]) == 0  # nothing checkpointed, so the retry is exact
