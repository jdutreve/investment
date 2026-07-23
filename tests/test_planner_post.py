"""Planner Post — Call 2 guardrail (docs/TASKS.md Task 4.2;
src/investment/planner/post.py). The deterministic guardrail directly (the
"hallucinations die" core); the LLM round-trip via PydanticAI TestModel."""

from pydantic_ai.models.test import TestModel

from investment.planner.context import PlannerContext
from investment.planner.post import (
    Confrontation,
    PlannerPost,
    PostPlannerResult,
    apply_guardrail,
    known_context,
)
from investment.worker.result import EvaluationDraft, ScenarioAdjustment, WorkerResult


def _context() -> PlannerContext:
    return PlannerContext(
        regime={"regime_name": "Stagflation", "regime_type_id": "stag"},
        global_liquidity={},
        ranking=[{"portfolio_id": "pf1"}],
        scenarios=[{"strategy_id": "s1", "scenario": "bull"}],
        top_invariants=[{"id": "inv-gold", "title": "gold beats in stagflation"}],
        recent_proposals=[],
        passages=[],
        notes="",
    )


def _eval(strategy_id: str, verdict: str, events: list[str]) -> EvaluationDraft:
    return EvaluationDraft(
        strategy_id=strategy_id, verdict=verdict, conviction_delta=0.0, events=events, reasoning="r"
    )


# -- known_context -----------------------------------------------------------


def test_known_context_collects_ids_and_evidence_tokens() -> None:
    kc = known_context(_context())
    assert kc.strategies == {"s1"}
    assert kc.invariants == {"inv-gold"}
    assert kc.portfolios == {"pf1"}
    # tokens include ids, invariant title words, regime + scenario names
    assert {"s1", "inv-gold", "stag", "bull", "gold"} <= kc.tokens


# -- the four guardrail behaviours -------------------------------------------


def test_unevidenced_verdict_is_downgraded_to_neutral() -> None:
    result = PostPlannerResult(
        evaluations=[_eval("s1", "confirms", ["markets felt calm this week"])]
    )
    cleaned = apply_guardrail(result, _context())
    assert cleaned.evaluations[0].verdict == "neutral"
    assert "downgraded s1" in cleaned.regime_notes


def test_evidenced_verdict_survives() -> None:
    # the event mentions 'stag' (the regime) — traceable to the context
    result = PostPlannerResult(
        evaluations=[_eval("s1", "confirms", ["stag regime deepened, gold led"])]
    )
    cleaned = apply_guardrail(result, _context())
    assert cleaned.evaluations[0].verdict == "confirms"
    assert cleaned.regime_notes == ""


def test_unknown_strategy_evaluation_is_dropped() -> None:
    result = PostPlannerResult(evaluations=[_eval("ghost", "invalidates", ["stag"])])
    cleaned = apply_guardrail(result, _context())
    assert cleaned.evaluations == []
    assert "unknown strategy ghost" in cleaned.regime_notes


def test_scenario_update_without_a_trigger_is_dropped() -> None:
    result = PostPlannerResult(
        scenario_updates=[
            ScenarioAdjustment(strategy_id="s1", scenario="bull", probability=60.0, rationale=""),
            ScenarioAdjustment(
                strategy_id="s1", scenario="bear", probability=20.0, rationale="curve inverted"
            ),
        ]
    )
    cleaned = apply_guardrail(result, _context())
    assert [s.scenario for s in cleaned.scenario_updates] == ["bear"]
    assert "no trigger named" in cleaned.regime_notes


def test_confrontation_of_unknown_invariant_is_dropped() -> None:
    result = PostPlannerResult(
        confrontations=[
            Confrontation(invariant_id="inv-gold", verdict="confirmed"),
            Confrontation(invariant_id="inv-ghost", verdict="refuted"),
        ]
    )
    cleaned = apply_guardrail(result, _context())
    assert [c.invariant_id for c in cleaned.confrontations] == ["inv-gold"]
    assert "unknown invariant inv-ghost" in cleaned.regime_notes


def test_a_clean_result_is_left_untouched() -> None:
    result = PostPlannerResult(regime_notes="all coherent")
    cleaned = apply_guardrail(result, _context())
    assert cleaned.regime_notes == "all coherent"  # no GUARDRAIL flags appended


# -- LLM round-trip ----------------------------------------------------------


async def test_run_extracts_then_guardrails() -> None:
    post = PlannerPost("planner/x", "sk-test")
    worker = WorkerResult(regime_assessment="a", ranking_commentary="b", reasoning="c")
    # TestModel returns an evaluation with an unknown strategy -> guardrail drops it
    forced = TestModel(
        custom_output_args={
            "evaluations": [
                {
                    "strategy_id": "ghost",
                    "verdict": "confirms",
                    "conviction_delta": 0.0,
                    "events": ["x"],
                    "reasoning": "r",
                }
            ],
            "scenario_updates": [],
            "confrontations": [],
            "innovations": [],
            "regime_notes": "",
        }
    )
    with post.agent.override(model=forced):
        result = await post.run(worker, _context())
    assert isinstance(result, PostPlannerResult)
    assert result.evaluations == []  # the guardrail dropped the unknown-strategy eval
    assert "unknown strategy ghost" in result.regime_notes
