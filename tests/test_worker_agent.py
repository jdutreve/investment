"""Worker agent + result contract (docs/TASKS.md Phase 5 "Worker agent";
docs/ARCHITECTURE.md "WORKER"). The deterministic core is tested here; the LLM
round-trip uses PydanticAI's TestModel — its own transport double, not a mock
of our code (CLAUDE.md forbids mocking OUR components, e.g. the DB, which stays
a real throwaway SQLite below)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

from investment.db.sqlite import InvestmentDB
from investment.worker.agent import WORKER_SYSTEM_PROMPT, build_worker_agent, run_worker
from investment.worker.result import (
    ImprovementProposal,
    ImprovementType,
    ReallocationProposal,
    ScenarioAdjustment,
    WorkerResult,
)

# -- the result contract: the empty state is EXPLICIT, not forgotten ---------


def test_worker_result_empty_state_defaults() -> None:
    """"Nothing to propose" is None / [], present on every run — the schema
    makes the empty state a value, not a missing field (docs/ARCHITECTURE.md:
    "always complete, fields possibly empty")."""
    r = WorkerResult(regime_assessment="calm", ranking_commentary="as ranked", reasoning="—")
    assert r.reallocation_proposed is None
    assert r.innovations_proposed == []
    assert r.scenario_adjustments == []
    assert r.evaluations == []


def test_scenario_probability_is_bounded() -> None:
    """A scenario probability outside 0-100 cannot even be constructed — the
    3-must-sum-to-100 rule lives in Writeback, but a 140 is a schema error."""
    with pytest.raises(ValidationError):
        ScenarioAdjustment(strategy_id="s", scenario="bull", probability=140.0, rationale="x")


def test_reallocation_and_innovation_round_trip() -> None:
    r = WorkerResult(
        regime_assessment="stagflation building",
        ranking_commentary="defender leads on Sortino",
        reallocation_proposed=ReallocationProposal(
            proposed_allocation={"GLD": 50.0, "VCIT": 50.0},
            scenario_delta={"GLD": 10.0},
            favors_delta={"GLD": 5.0},
            blend_note="0.4 tactical + 0.6 structural",
            supporting_invariants=["inv-gold-ratio-trend-tilt"],
            reasoning="gold above its 7y trend and rising",
        ),
        innovations_proposed=[
            ImprovementProposal(
                type=ImprovementType.new_invariant,
                title="t",
                rationale="r",
                spec={"condition": []},
                weight_initial=0.5,
                floor_weight=0.2,
                trace="tr",
            )
        ],
        reasoning="—",
    )
    assert r.reallocation_proposed is not None
    assert r.reallocation_proposed.supporting_invariants == ["inv-gold-ratio-trend-tilt"]
    assert r.innovations_proposed[0].type is ImprovementType.new_invariant
    # author/status default to the floor-tier + proposed convention
    assert r.innovations_proposed[0].author == "system"
    assert r.innovations_proposed[0].status == "proposed"


# -- the agent: it builds, and it round-trips to a valid WorkerResult --------


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "worker.db")
    yield conn
    await conn.close()


def test_build_worker_agent_registers_exactly_the_three_tools(db: InvestmentDB) -> None:
    """Least privilege: the Worker gets db_query / market_fetch /
    portfolio_check and nothing else (docs/ARCHITECTURE.md WORKER)."""
    agent = build_worker_agent(db, "anthropic/claude-sonnet-5", "sk-test")
    tool_names = set(agent._function_toolset.tools)
    assert tool_names == {"db_query", "market_fetch", "portfolio_check"}


def test_persona_states_the_unawareness_the_tools_enforce() -> None:
    """The prompt asserts what the tool boundary makes true — the Worker is
    unaware of the Planner/Writeback/storage (docs/ARCHITECTURE.md)."""
    assert "unaware of the Planner, Writeback, and internal storage" in WORKER_SYSTEM_PROMPT
    assert "do not recalculate" in WORKER_SYSTEM_PROMPT


async def test_round_trip_returns_a_valid_worker_result(db: InvestmentDB) -> None:
    """The Phase-5 Definition of Done: a Worker round-trip returns a valid
    WorkerResult. TestModel drives the agent's output path (call_tools=[] keeps
    it to the structured output — the tools themselves are covered in
    test_worker_tools.py)."""
    agent = build_worker_agent(db, "anthropic/claude-sonnet-5", "sk-test")
    with agent.override(model=TestModel(call_tools=[])):
        result = await run_worker(agent, "the prepared context")
    assert isinstance(result, WorkerResult)
    # TestModel fills required strings and defaults the optionals — i.e. the
    # empty state is reachable through the real output path, not just the model.
    assert result.innovations_proposed == []
    assert result.reallocation_proposed is None
