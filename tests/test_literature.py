"""The owner's literature claims (ADR-006 amendment 2026-11-15): filed with their
sources, never measured, weighed or re-judged, and read whole by the Worker."""

import dataclasses
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from investment.db.sqlite import InvestmentDB
from investment.decision_cycle import render_context_for_worker
from investment.mechanical import invariants
from investment.planner.context import PlannerContext
from investment.writeback.literature import (
    LITERATURE_EVENT,
    file_literature_claim,
    literature_claims,
)

TODAY = date(2026, 11, 16)


class _Embedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 384), dtype=np.float32)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "literature.db")
    yield conn
    await conn.close()


async def _file(db: InvestmentDB) -> str:
    return await file_literature_claim(
        db,
        _Embedder(),
        title="Expensive equities return less over the next ten years",
        description="A high cyclically adjusted P/E is followed by low 10-year real returns.",
        sources=["Campbell & Shiller (1998)"],
        today=TODAY,
    )


async def test_a_claim_is_journalled_then_filed_without_a_weight(db: InvestmentDB) -> None:
    iid = await _file(db)
    row = (await db.query("SELECT * FROM invariant WHERE id = :i", i=iid))[0]
    assert row["status"] == invariants.LITERATURE_STATUS
    assert row["weight_effective"] is None
    assert "Campbell & Shiller (1998)" in row["example"]
    events = await db.query("SELECT source_id FROM event_log WHERE type = :t", t=LITERATURE_EVENT)
    assert [e["source_id"] for e in events] == [iid]


async def test_a_claim_without_sources_is_refused(db: InvestmentDB) -> None:
    with pytest.raises(ValueError, match="sources"):
        await file_literature_claim(
            db, _Embedder(), title="t", description="d", sources=[], today=TODAY
        )


async def test_the_engine_neither_matures_nor_restates_it(db: InvestmentDB) -> None:
    """The birth sweep would file an effect-less row as a reference note, and the
    weekly restatement would judge it 'proposed' on an empty record."""
    iid = await _file(db)
    row = (await db.query("SELECT * FROM invariant WHERE id = :i", i=iid))[0]
    result = await invariants._mature_one(db, row, None, True)  # type: ignore[arg-type]
    assert result.status == invariants.LITERATURE_STATUS
    assert await invariants.restate_invariant(db, iid, {}, TODAY) == invariants.LITERATURE_STATUS
    after = (await db.query("SELECT status, trace FROM invariant WHERE id = :i", i=iid))[0]
    assert after["status"] == invariants.LITERATURE_STATUS
    assert "NOT CERTIFIED" not in after["trace"]


async def test_the_worker_reads_every_claim_and_no_weight(db: InvestmentDB) -> None:
    await _file(db)
    context = PlannerContext(
        regime={},
        global_liquidity={},
        ranking=[],
        scenarios=[],
        top_invariants=[],
        recent_proposals=[],
        passages=[],
        notes="",
    )
    text = render_context_for_worker(
        dataclasses.replace(context, literature=await literature_claims(db))
    )
    assert "FROM THE LITERATURE" in text
    assert "Expensive equities return less over the next ten years" in text
    assert "Campbell & Shiller (1998)" in text
