"""Worker agent + result contract (docs/TASKS.md Phase 5 "Worker agent";
docs/ARCHITECTURE.md "WORKER"). The deterministic core is tested here; the LLM
round-trip uses PydanticAI's TestModel — its own transport double, not a mock
of our code (CLAUDE.md forbids mocking OUR components, e.g. the DB, which stays
a real throwaway SQLite below)."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits

from investment.db.sqlite import InvestmentDB
from investment.worker.agent import (
    OUTPUT_RETRIES,
    SKILL_ORDER,
    SKILLS_DIR,
    WORKER_REQUEST_LIMIT,
    WORKER_SYSTEM_PROMPT,
    WORKER_TOOL_CALLS_LIMIT,
    build_system_prompt,
    build_worker_agent,
    load_skills,
    run_worker,
)
from investment.worker.result import (
    ImprovementProposal,
    ImprovementType,
    ReallocationProposal,
    ScenarioAdjustment,
    WorkerResult,
)

# -- the result contract: the empty state is EXPLICIT, not forgotten ---------


def test_worker_result_empty_state_is_a_value_not_an_omission() -> None:
    """ "Nothing to propose" is None / [] SAID EXPLICITLY (docs/ARCHITECTURE.md:
    "always complete, fields possibly empty"). The empty state stays
    constructible; what is no longer allowed is reaching it by leaving the key
    out, which made "nothing to propose" and "forgot the field"
    indistinguishable."""
    r = WorkerResult(
        regime_assessment="calm",
        ranking_commentary="as ranked",
        market_signal_assessment="the book fits the spread",
        reasoning="—",
        scenario_adjustments=[],
        evaluations=[],
        reallocation_proposed=None,
        innovations_proposed=[],
    )
    assert r.reallocation_proposed is None
    assert r.innovations_proposed == []
    assert r.scenario_adjustments == []
    assert r.evaluations == []


def test_an_omitted_field_fails_validation_rather_than_defaulting() -> None:
    """The Phase-1bis policy the docstring claims: a partial answer FAILS
    validation instead of passing silently. PydanticAI feeds the error back as a
    retry, so the Worker is told what is missing."""
    with pytest.raises(ValidationError):
        WorkerResult(
            regime_assessment="calm",
            ranking_commentary="as ranked",
            market_signal_assessment="the book fits the spread",
            reasoning="—",
        )


def test_an_unknown_field_is_refused_not_dropped() -> None:
    """pydantic's default is to DISCARD unknown keys: a plausible-looking
    misspelling validated clean and the Worker's real answer vanished."""
    with pytest.raises(ValidationError):
        ScenarioAdjustment(
            strategy_id="s",
            scenario="bull",
            probability=40.0,
            rationale="x",
            confidence_pct=90.0,  # type: ignore[call-arg]
        )


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
        scenario_adjustments=[],
        evaluations=[],
    )
    assert r.reallocation_proposed is not None
    assert r.reallocation_proposed.supporting_invariants == ["inv-gold-ratio-trend-tilt"]
    assert r.innovations_proposed[0].type is ImprovementType.new_invariant
    # author/status default to the floor-tier + proposed convention
    assert r.innovations_proposed[0].author == "system"
    assert r.innovations_proposed[0].status == "proposed"


# -- the agent: it builds, and it round-trips to a valid WorkerResult --------


_OUTPUT = object()


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "worker.db")
    yield conn
    await conn.close()


def test_build_worker_agent_registers_exactly_the_three_tools(db: InvestmentDB) -> None:
    """Least privilege: the Worker gets db_query / market_fetch /
    portfolio_check and nothing else (docs/ARCHITECTURE.md WORKER)."""
    agent = build_worker_agent(db, "test/worker", "sk-test")
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
    agent = build_worker_agent(db, "test/worker", "sk-test")
    with agent.override(model=TestModel(call_tools=[])):
        result = await run_worker(agent, "the prepared context")
    # A schema-valid WorkerResult came back through the real output path. The
    # empty state is no longer asserted here: with the fields required, TestModel
    # POPULATES them with samples rather than defaulting them, so this test can
    # only speak to validity — the empty state is covered directly above.
    assert isinstance(result, WorkerResult)


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


# -- the agentic-loop budget -------------------------------------------------


async def test_a_runaway_tool_loop_is_stopped_by_the_budget(db: InvestmentDB) -> None:
    """The one unbounded cost in the system: the tools are ROW-capped but were
    not CALL-capped, so a Worker that kept calling `db_query` ran until the 300s
    timeout, billing every turn. It must FAIL, not degrade — a half-cycle
    silently written to the graph is worse than a Monday with no cognitive read.
    """
    agent = build_worker_agent(db, "test/worker", "sk-test")
    # TestModel(call_tools='all') calls every registered tool, then answers; the
    # budget is squeezed below that to force the runaway path deterministically.
    with agent.override(model=TestModel()), pytest.raises(UsageLimitExceeded):
        await run_worker_with_limits(agent, "ctx", tool_calls_limit=1, request_limit=2)


async def test_the_shipped_budget_does_not_bite_a_normal_cycle(db: InvestmentDB) -> None:
    """The guard is a RUNAWAY bound, not a frugality knob. `call_tools=[]` keeps
    this to the structured-output path — TestModel's synthetic tool ARGS are
    rejected by the real tool validators long before any budget, so a
    tools-and-all happy path cannot be simulated here; the runaway case above is
    what the budget exists for, and this pins that it stays out of the way."""
    agent = build_worker_agent(db, "test/worker", "sk-test")
    with agent.override(model=TestModel(call_tools=[])):
        result = await run_worker(agent, "ctx")
    assert isinstance(result, WorkerResult)


def test_the_shipped_budget_leaves_room_for_the_answer_and_its_retries() -> None:
    """`request_limit` must sit ABOVE `tool_calls_limit`: each tool call costs a
    request and the final structured answer costs one more, plus the schema
    retries. Equal limits would fail the cycle on its last, correct turn."""
    assert WORKER_REQUEST_LIMIT > WORKER_TOOL_CALLS_LIMIT + OUTPUT_RETRIES


async def run_worker_with_limits(
    agent: object, context: str, *, tool_calls_limit: int, request_limit: int
) -> WorkerResult:
    """`run_worker` with a squeezed budget — the shipped one is deliberately
    generous, so the runaway path needs a tighter bound to be provoked."""
    result = await agent.run(  # type: ignore[attr-defined]
        context,
        usage_limits=UsageLimits(request_limit=request_limit, tool_calls_limit=tool_calls_limit),
    )
    return result.output  # type: ignore[no-any-return]


# -- the skill files (docs/TASKS.md Phase 5) ---------------------------------


def test_every_skill_file_is_ordered_explicitly() -> None:
    """`SKILL_ORDER` is the contract; a file on disk but missing from it would
    still load (appended last) but in the wrong place, which is invisible."""
    assert {p.name for p in SKILLS_DIR.glob("*.md")} == set(SKILL_ORDER)


def test_the_live_path_leads_the_prompt() -> None:
    """The Worker's ONLY contribution to the adopted allocation is its reading of
    the mechanical decision. Alphabetically that skill was third; TASKS' pre-pivot
    list did not have it at all."""
    assert SKILL_ORDER[0] == "skill-read-market-signal.md"
    skills = load_skills()
    assert skills.index("read the market-signal decision") < skills.index("retained bridge")


def test_an_unlisted_skill_is_appended_not_dropped(tmp_path: Path) -> None:
    """Silently never reaching the prompt is the hardest kind of bug to see:
    everything runs, the guidance is simply absent."""
    (tmp_path / "skill-brand-new.md").write_text("BRAND NEW GUIDANCE")
    (tmp_path / "skill-read-market-signal.md").write_text("LIVE PATH")
    loaded = load_skills(tmp_path)
    assert loaded.index("LIVE PATH") < loaded.index("BRAND NEW GUIDANCE")


def test_the_skills_carry_the_contracts_the_gates_enforce() -> None:
    """A skill that taught something the gates refuse would spend cycles on
    retries. These four are each ALSO enforced mechanically, so the prompt and
    the gate must agree."""
    skills = load_skills()
    assert "[-10, +10]" in skills  # EvaluationDraft's Field bounds
    assert "neutral" in skills  # the fourth Literal verdict
    assert "0.4 x scenario_delta + 0.6 x favors_delta" in skills  # the blend
    assert "bright" in skills and "active" in skills  # gate 6's exact pair


def test_the_bridge_skill_says_it_is_the_bridge() -> None:
    """The docs cleanup's point, applied where a model actually reads it: the
    superseded path must not read as live instruction."""
    text = (SKILLS_DIR / "skill-the-retained-bridge.md").read_text()
    assert "NONE OF THIS IS THE LIVE ALLOCATION" in text
    assert "ADR-011" in text  # stated where the reallocation is described


def test_the_live_skill_separates_an_assessment_from_a_rule_challenge() -> None:
    """The distinction the whole ADR-011 channel rests on: a reading goes in
    `market_signal_assessment`, a disagreement with the RULE goes in
    `innovations_proposed` as a strategy_revision. Collapsing the two either
    loses the objection or wastes a probation slot."""
    text = (SKILLS_DIR / "skill-read-market-signal.md").read_text()
    assert "market_signal_assessment" in text
    assert "strategy_revision" in text
    assert "Do not re-pick the book" in text


def test_the_prompt_carries_the_skills() -> None:
    prompt = build_system_prompt()
    assert prompt.startswith(WORKER_SYSTEM_PROMPT)
    assert "# SKILLS" in prompt
    assert "lighthouses, not orders" in prompt


def test_a_missing_skills_directory_degrades_rather_than_breaks(tmp_path: Path) -> None:
    """The prompt teaches; the GATES decide. Every contract the skills describe
    is enforced mechanically too, so a missing file must not make the cycle
    unsafe — or unrunnable."""
    assert load_skills(tmp_path / "nope") == ""
    assert build_system_prompt(skills="") == WORKER_SYSTEM_PROMPT


async def test_a_cut_call_is_re_asked_immediately_not_left_to_the_date(
    db: InvestmentDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagnosed 2026-08-08: `JSONDecodeError: Expecting value: line 3709
    column 1`, raised 12m57s after the previous successful call — a body VALID
    for 3708 lines and then cut. A truncated stream, not a model writing bad
    JSON.

    Nothing caught it early because nothing watched the TOTAL: httpx's read
    timeout re-arms on every chunk, PydanticAI's retries apply only after a
    successful parse, and the OpenAI SDK does not retry a 200 with an incomplete
    body. So a call dribbled for thirteen minutes and took the date with it.

    Re-asked HERE, not at the date level, because
    `agentic_replay._cycle_with_retry` never retries a TimeoutError — right for
    "the date's budget ran out", wrong for "one call was cut with twenty minutes
    left"."""
    calls = {"n": 0}
    agent = build_worker_agent(db, "test/worker", "sk-test")

    async def _truncated_then_fine(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise json.JSONDecodeError("Expecting value", "{", 3709)
        return SimpleNamespace(output=_OUTPUT)

    monkeypatch.setattr(agent, "run", _truncated_then_fine)
    out = await run_worker(agent, "context")

    assert calls["n"] == 2  # cut once, re-asked once, answered
    assert out is _OUTPUT
