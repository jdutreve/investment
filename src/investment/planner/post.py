"""Planner Post — Call 2, the knowledge extractor + guardrail (docs/ARCHITECTURE.md
"CALL 2 — Knowledge Extractor"; docs/TASKS.md Task 4.2). Runs after the Worker;
Writeback commits what it returns.

Call 2 (LLM) turns the Worker's analysis, read against the PlannerContext, into
a committable PostPlannerResult (evaluations, scenario_updates, confrontations,
innovations, regime_notes). Then `apply_guardrail` — the deterministic part,
"where hallucinations die" (docs/TASKS.md) — cleans it:

- an EvaluationDraft whose verdict is not 'neutral' but cites no event traceable
  to the context is DOWNGRADED to 'neutral' and flagged (a verdict with no
  evidence is an opinion, not a finding);
- any reference to an id absent from the context — an unknown strategy,
  invariant, or portfolio — is DROPPED and flagged (never act on something that
  was not in front of the Worker);
- a scenario update that names no qualitative trigger (empty rationale) is
  dropped and flagged.

The guardrail never raises: it degrades to a smaller, honest result and records
why in regime_notes (the digest shows it). Only an unparseable LLM output —
caught by PydanticAI's schema retries, not here — aborts the UC8 step.
"""

import dataclasses
import logging

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from investment.planner.context import PlannerContext
from investment.worker.result import (
    EvaluationDraft,
    ImprovementProposal,
    ScenarioAdjustment,
    WorkerResult,
)

logger = logging.getLogger(__name__)

TRANSPORT_RETRIES = 2
OUTPUT_RETRIES = 2
POST_TIMEOUT_SECONDS = 120.0
POST_REASONING_EFFORT = "high"  # extraction + guardrail is the careful pass


class Confrontation(BaseModel):
    """A confrontation Call 2 derived from an evaluation — an invariant the
    Worker's finding confirms or refutes. Writeback logs it source='evaluation'
    (docs/ARCHITECTURE.md confrontation rule); the invariant must be one shown
    in the context (the guardrail enforces it)."""

    invariant_id: str
    verdict: str  # 'confirmed' | 'refuted'
    note: str = ""


class PostPlannerResult(BaseModel):
    """Call 2's output (docs/ARCHITECTURE.md CALL 2). `regime_notes` also carries
    the guardrail's flags after `apply_guardrail` runs."""

    evaluations: list[EvaluationDraft] = Field(default_factory=list)
    scenario_updates: list[ScenarioAdjustment] = Field(default_factory=list)
    confrontations: list[Confrontation] = Field(default_factory=list)
    innovations: list[ImprovementProposal] = Field(default_factory=list)
    regime_notes: str = ""


# -- pure core: the guardrail ------------------------------------------------


@dataclasses.dataclass(frozen=True)
class KnownContext:
    """What the Worker was actually shown — the sets a claim is allowed to
    reference, plus a token bag for evidence matching."""

    strategies: frozenset[str]
    invariants: frozenset[str]
    portfolios: frozenset[str]
    tokens: frozenset[str]  # lowercased ids + names for the evidence check


def known_context(context: PlannerContext) -> KnownContext:
    strategies = {str(s["strategy_id"]) for s in context.scenarios}
    invariants = {str(i["id"]) for i in context.top_invariants}
    portfolios = {str(r["portfolio_id"]) for r in context.ranking}

    tokens: set[str] = {s.lower() for s in strategies | invariants | portfolios}
    for inv in context.top_invariants:
        tokens.update(str(inv.get("title", "")).lower().split())
    for scenario in context.scenarios:
        tokens.add(str(scenario["scenario"]).lower())
    for key in ("regime_type_id", "regime_name"):
        value = context.regime.get(key)
        if value:
            tokens.add(str(value).lower())
    tokens.discard("")
    return KnownContext(
        frozenset(strategies), frozenset(invariants), frozenset(portfolios), frozenset(tokens)
    )


def _is_evidenced(events: list[str], tokens: frozenset[str]) -> bool:
    """An evaluation is evidenced iff at least one of its events mentions a
    token that was actually in the context — an id, a name, a regime, a
    scenario. A free-text event with no anchor to what the Worker saw is not
    traceable evidence."""
    text = " ".join(events).lower()
    return any(token in text for token in tokens)


def apply_guardrail(result: PostPlannerResult, context: PlannerContext) -> PostPlannerResult:
    """Clean an extracted result against the context (docs/TASKS.md Task 4.2
    guardrail). Never raises — drops/downgrades and folds the reasons into
    regime_notes."""
    kc = known_context(context)
    flags: list[str] = []

    evaluations: list[EvaluationDraft] = []
    for ev in result.evaluations:
        if ev.strategy_id not in kc.strategies:
            flags.append(f"dropped evaluation of unknown strategy {ev.strategy_id}")
            continue
        if ev.verdict != "neutral" and not _is_evidenced(ev.events, kc.tokens):
            flags.append(
                f"downgraded {ev.strategy_id} verdict '{ev.verdict}' -> neutral: "
                "no event traceable to the context"
            )
            ev = ev.model_copy(update={"verdict": "neutral"})
        evaluations.append(ev)

    scenario_updates: list[ScenarioAdjustment] = []
    for sc in result.scenario_updates:
        if sc.strategy_id not in kc.strategies:
            flags.append(f"dropped scenario update for unknown strategy {sc.strategy_id}")
            continue
        if not sc.rationale.strip():
            flags.append(
                f"dropped scenario update {sc.strategy_id}/{sc.scenario}: no trigger named"
            )
            continue
        scenario_updates.append(sc)

    confrontations: list[Confrontation] = []
    for cf in result.confrontations:
        if cf.invariant_id not in kc.invariants:
            flags.append(f"dropped confrontation of unknown invariant {cf.invariant_id}")
            continue
        confrontations.append(cf)

    notes = result.regime_notes
    if flags:
        notes = (notes + "\n" if notes else "") + "GUARDRAIL: " + "; ".join(flags)

    return PostPlannerResult(
        evaluations=evaluations,
        scenario_updates=scenario_updates,
        confrontations=confrontations,
        # innovation validity is Writeback's gate, not the guardrail's
        innovations=result.innovations,
        regime_notes=notes,
    )


# -- rendering + agent -------------------------------------------------------


EXTRACT_INSTRUCTIONS = """\
You are the cognitive coach reviewing the expert's analysis. Extract the
committable knowledge from the WORKER RESULT, read against the CONTEXT, as a
PostPlannerResult:

- evaluations: per strategy, whether the evidence confirms/weakens/invalidates
  its thesis, or is neutral. Every non-neutral verdict MUST cite, in `events`,
  a data point that appears in the context — a strategy, invariant, regime, or
  scenario named there. Do not assert a verdict you cannot ground.
- scenario_updates: probability shifts, each NAMING the qualitative trigger it
  interprets (an empty rationale will be dropped).
- confrontations: invariants the findings confirm or refute — only invariants
  present in the context.
- innovations: new invariants/strategies the analysis proposes.
- regime_notes: your framing, and any contradiction you see between the Worker's
  claims and the baseline data.

Reference ONLY ids present in the context. Anything else will be dropped."""


def _render(worker_result: WorkerResult, context: PlannerContext) -> str:
    known = ", ".join(sorted({str(s["strategy_id"]) for s in context.scenarios}))
    invariants = ", ".join(str(i["id"]) for i in context.top_invariants)
    return (
        f"REGIME: {context.regime.get('regime_name')} ({context.regime.get('regime_type_id')})\n"
        f"KNOWN STRATEGIES: {known}\n"
        f"CONTEXT INVARIANTS: {invariants}\n\n"
        f"WORKER RESULT:\n{worker_result.model_dump_json(indent=2)}"
    )


def build_extract_agent(
    model_name: str,
    api_key: str,
    *,
    reasoning_effort: str = POST_REASONING_EFFORT,
    base_url: str = "https://openrouter.ai/api/v1",
) -> Agent[None, PostPlannerResult]:
    provider = OpenRouterProvider(
        openai_client=AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=TRANSPORT_RETRIES,
            timeout=POST_TIMEOUT_SECONDS,
        )
    )
    return Agent(
        OpenAIChatModel(model_name, provider=provider),
        output_type=PostPlannerResult,
        instructions=EXTRACT_INSTRUCTIONS,
        retries=OUTPUT_RETRIES,
        model_settings=OpenAIChatModelSettings(
            timeout=POST_TIMEOUT_SECONDS,
            openai_reasoning_effort=reasoning_effort,  # type: ignore[typeddict-item]
        ),
    )


class PlannerPost:
    """Call 2 + guardrail for one UC8 cycle. Built once, run per cycle; tests
    `.override(model=TestModel(...))` the agent."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        *,
        reasoning_effort: str = POST_REASONING_EFFORT,
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        self.agent = build_extract_agent(
            model_name, api_key, reasoning_effort=reasoning_effort, base_url=base_url
        )

    async def run(
        self, worker_result: WorkerResult, context: PlannerContext
    ) -> PostPlannerResult:
        """Extract, then guardrail. The returned result is always coherent with
        the context — Writeback commits it as-is."""
        extracted = await self.agent.run(_render(worker_result, context))
        return apply_guardrail(extracted.output, context)
