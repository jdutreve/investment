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
    """ "Nothing to propose" is None / [], present on every run — the schema
    makes the empty state a value, not a missing field (docs/ARCHITECTURE.md:
    "always complete, fields possibly empty")."""
    r = WorkerResult(
        regime_assessment="calm",
        ranking_commentary="as ranked",
        market_signal_assessment="the book fits the spread",
        reasoning="—",
    )
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
        market_signal_assessment="the steep-curve book is defensible; watch the haven crowding",
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


def test_the_persona_names_the_field_its_contribution_must_land_in() -> None:
    """ADR-011: the prompt asks for a reading of the mechanical decision ("that
    reading is your contribution"). It must also say WHERE it goes, or the model
    puts it in `reasoning` and the journal and digest never find it — which is
    exactly how the promise went unkept before `market_signal_assessment`
    existed."""
    assert "that reading is your contribution" in WORKER_SYSTEM_PROMPT
    assert "market_signal_assessment" in WORKER_SYSTEM_PROMPT
    assert "market_signal_assessment" in WorkerResult.model_fields


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


# -- the allocation contract at the LLM boundary -----------------------------


def _realloc(**over: object) -> ReallocationProposal:
    fields: dict[str, object] = {
        "proposed_allocation": {"GLD": 50.0, "VCIT": 50.0},
        "scenario_delta": {"GLD": 10.0},
        "favors_delta": {"GLD": -5.0},  # a NEGATIVE delta is legal: it is a change
        "blend_note": "b",
        "supporting_invariants": [],
        "reasoning": "r",
    }
    fields.update(over)
    return ReallocationProposal(**fields)  # type: ignore[arg-type]


def test_a_negative_weight_is_rejected_at_the_boundary() -> None:
    """V1 is long-only. Rejected HERE and not only by the gate, because a
    validation error is fed back to the model as a retry — the Worker is told
    what is wrong and answers again, instead of losing the cycle to a silent ⛔."""
    with pytest.raises(ValidationError, match="long-only"):
        _realloc(proposed_allocation={"GLD": 130.0, "VCIT": -30.0})


def test_non_finite_weights_are_rejected_everywhere_they_can_appear() -> None:
    """JSON's `NaN`/`Infinity` tokens parse, and a NaN weight is invisible to
    every comparison-based gate downstream (mechanical/gates.py gate 0)."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            _realloc(proposed_allocation={"GLD": 50.0, "VCIT": bad})
        # deltas are recorded rather than gated, so a NaN there is persisted raw
        with pytest.raises(ValidationError, match="finite"):
            _realloc(scenario_delta={"GLD": bad})


def test_negative_deltas_stay_legal() -> None:
    """The long-only rule binds the ALLOCATION, never the deltas: a delta is a
    change, and a negative one is how a sleeve is cut."""
    assert _realloc(favors_delta={"GLD": -12.5}).favors_delta == {"GLD": -12.5}
