"""Planner Pre orchestration (docs/TASKS.md Task 4.1;
src/investment/planner/pre.py). The two LLM calls are driven by PydanticAI's
TestModel (its transport double) so the baseline -> Call 1a -> retrieve ->
Call 1b -> assemble wiring runs end to end against a real throwaway SQLite, with
a stub embedder in place of the multi-second model load."""

from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel

from investment.db.sqlite import InvestmentDB
from investment.planner.context import PlannerContext
from investment.planner.pre import PlannerPre
from investment.worker.tools import WorkerTools


class _StubEmbedder:
    """Records what it was asked to embed; returns zero vectors of a fixed dim
    (their cosines are 0, so retrieval degrades to empty on an empty corpus —
    exactly what these wiring tests want)."""

    def __init__(self, dims: int = 4) -> None:
        self.calls: list[list[str]] = []
        self._dims = dims

    def encode(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return np.zeros((len(texts), self._dims), dtype=np.float32)


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO regime_type (id, name, aliases, framework_id, description, created_at) "
        "VALUES ('stag', 'Stagflation', '[]', '4s', 'd', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO regime (id, regime_type_id, tags, start_date, is_current, events, trace, "
        "created_at, updated_at) VALUES ('r1', 'stag', '[]', '2026-06-01', 1, '[]', 't', "
        "'2026-06-01', '2026-06-01')"
    )


@pytest.fixture
async def pre(tmp_path: Path) -> AsyncIterator[PlannerPre]:
    db = InvestmentDB(tmp_path / "pre.db")
    await _seed(db)
    yield PlannerPre(db, _StubEmbedder(), "planner/x", "sk-test")
    await db.close()


async def test_run_assembles_a_planner_context_end_to_end(pre: PlannerPre) -> None:
    query = TestModel(custom_output_args={"corpus_queries": ["regime shift"], "zooms": []})
    select = TestModel(
        custom_output_args={"invariant_ids": [], "passage_ids": [], "notes": "quiet week"}
    )
    with pre.query_agent.override(model=query), pre.context_agent.override(model=select):
        context, registry = await pre.run("weekly")

    assert isinstance(context, PlannerContext)
    assert context.regime["regime_type_id"] == "stag"  # baseline passes through
    assert context.top_invariants == []  # empty selection -> empty
    assert context.notes == "quiet week"
    assert isinstance(registry, WorkerTools)
    # the Call 1a corpus_queries were actually embedded on the way to retrieval
    assert pre._embedder.calls == [["regime shift"]]  # type: ignore[attr-defined]


async def test_call1b_rejects_an_invented_id(pre: PlannerPre) -> None:
    query = TestModel(custom_output_args={"corpus_queries": [], "zooms": []})
    # 'ghost' is in no pool (the corpus is empty) -> the output_validator raises
    # ModelRetry every attempt -> retries exhaust rather than admitting the id.
    select = TestModel(
        custom_output_args={"invariant_ids": ["ghost"], "passage_ids": [], "notes": "x"}
    )
    with (
        pre.query_agent.override(model=query),
        pre.context_agent.override(model=select),
        pytest.raises(UnexpectedModelBehavior),
    ):
        await pre.run("weekly")
