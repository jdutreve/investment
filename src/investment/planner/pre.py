"""Planner Pre — the two LLM calls that wrap the mechanical margin
(docs/ARCHITECTURE.md "Detailed Planner Steps"; docs/TASKS.md Task 4.1). This is
now thin: baseline (baseline.py), retrieval (retrieval.py) and context assembly
(context.py) are all built; `pre.py` only orchestrates them behind Call 1a and
Call 1b.

Flow (docs/ARCHITECTURE.md):
  PYTHON baseline → summary → CALL 1a (QueryStrategies, the VARIABLE margin) →
  PYTHON embed queries + retrieve + zooms → CALL 1b (ContextSelection) →
  PYTHON active-now + assemble → (PlannerContext, tool_registry).

Two robustness knobs, split as everywhere else: `max_retries` on the HTTP
client covers transport faults; PydanticAI's `retries` covers schema-validation
faults (the "Phase-1bis policy"). The never-invent rule gets a THIRD guard — a
per-run output_validator on Call 1b that raises `ModelRetry` when the selection
names an id outside the fetched pool, so a hallucinated id is corrected by the
model, not silently dropped (docs/TASKS.md inclusion rule v; the drop in
`assemble_context` is only the belt-and-suspenders after retries exhaust).

Skill contracts inline as instructions (like `curator.render_instructions`),
not separate .md files: there is no skill-loading runtime yet, and an inline
contract is what the two agents actually receive. The embedder is INJECTED (not
built here) so the whole run is testable with a stub — no multi-second model
load — exactly as `retrieve` takes vectors rather than text.
"""

import dataclasses
import logging
from typing import Any, Protocol

import numpy as np
from openai import AsyncOpenAI
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from investment.db.sqlite import InvestmentDB
from investment.planner.baseline import gather_baseline
from investment.planner.context import (
    ContextSelection,
    PlannerContext,
    active_invariant_ids,
    assemble_context,
    invariant_pool,
    passage_pool,
    render_baseline_summary,
)
from investment.planner.retrieval import QueryStrategies, RetrievalPool, retrieve
from investment.worker.tools import WorkerTools

logger = logging.getLogger(__name__)

TRANSPORT_RETRIES = 2
OUTPUT_RETRIES = 2
PLANNER_TIMEOUT_SECONDS = 120.0
PLANNER_REASONING_EFFORT = "medium"  # the Planner assembles context, it does not decide


class QueryEmbedder(Protocol):
    """The one thing `pre.py` needs from the embedder: text → normalized
    vectors (corpus/embedding.py InProcessEmbedder satisfies it). A Protocol so
    a stub can stand in for the model in tests."""

    def encode(self, texts: list[str]) -> np.ndarray: ...


@dataclasses.dataclass(frozen=True)
class _Pools:
    """Call 1b deps — the id sets the output_validator checks the selection
    against (the never-invent gate). Per-run, so it rides `deps`, not a
    module-level registration."""

    invariant_ids: frozenset[str]
    passage_ids: frozenset[str]


CALL1A_INSTRUCTIONS = """\
You are the cognitive coach of an expert investment agent. You prepare the
optimal context; you never reason about the strategies themselves.

Given this week's baseline SUMMARY, choose the VARIABLE margin only — what is
worth pulling into context THIS week that the fixed baseline does not already
cover. Output a QueryStrategies:

- corpus_queries: up to 3 short search phrases for the knowledge corpus, driven
  by THIS week's deltas — a regime change or candidate, the biggest invariant
  weight movers, a rejected or lost proposal, fresh events. Empty is a valid
  answer: a quiet week needs no extra reading.
- zooms: up to 3 deep-dives, each a whitelisted kind + arg:
  strategy_history(strategy_id) | invariant_confrontations(invariant_id) |
  regime_history(N recent instances) | proposal_thread(proposal_id). Use a zoom
  only when the summary shows an anomaly worth depth. Empty is valid.

Pick for relevance to what CHANGED, not for volume."""

CALL1B_INSTRUCTIONS = """\
You are the cognitive coach preparing the expert's context. From the candidate
POOL below (already fetched — you may NOT introduce ids that are not in it),
select what the expert should see this week. Output a ContextSelection:

- invariant_ids: the ~12-15 most relevant invariants from the pool. ALWAYS keep
  any cited by a pending or recently-judged proposal (continuity with
  outcomes); prioritise invariants whose weight moved this week; dedupe.
- passage_ids: the passages worth showing, from the pool only.
- notes: a one-line "why included" per invariant, plus your framing of the week.

NEVER invent an id. Selecting an id absent from the pool will be rejected and
you will be asked to correct it."""


def _model(model_name: str, api_key: str, base_url: str) -> OpenAIChatModel:
    provider = OpenRouterProvider(
        openai_client=AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=TRANSPORT_RETRIES,
            timeout=PLANNER_TIMEOUT_SECONDS,
        )
    )
    return OpenAIChatModel(model_name, provider=provider)


def _settings(reasoning_effort: str) -> OpenAIChatModelSettings:
    return OpenAIChatModelSettings(
        timeout=PLANNER_TIMEOUT_SECONDS,
        openai_reasoning_effort=reasoning_effort,  # type: ignore[typeddict-item]
    )


def build_query_agent(
    model_name: str, api_key: str, *, reasoning_effort: str, base_url: str
) -> Agent[None, QueryStrategies]:
    """Call 1a — QueryStrategies (the variable margin). No tools, no deps."""
    return Agent(
        _model(model_name, api_key, base_url),
        output_type=QueryStrategies,
        instructions=CALL1A_INSTRUCTIONS,
        retries=OUTPUT_RETRIES,
        model_settings=_settings(reasoning_effort),
    )


def build_context_agent(
    model_name: str, api_key: str, *, reasoning_effort: str, base_url: str
) -> Agent[_Pools, ContextSelection]:
    """Call 1b — ContextSelection, with the never-invent output_validator bound
    to the per-run pool via deps."""
    agent: Agent[_Pools, ContextSelection] = Agent(
        _model(model_name, api_key, base_url),
        deps_type=_Pools,
        output_type=ContextSelection,
        instructions=CALL1B_INSTRUCTIONS,
        retries=OUTPUT_RETRIES,
        model_settings=_settings(reasoning_effort),
    )

    @agent.output_validator
    def _no_invented_ids(ctx: RunContext[_Pools], selection: ContextSelection) -> ContextSelection:
        missing = [i for i in selection.invariant_ids if i not in ctx.deps.invariant_ids]
        missing += [p for p in selection.passage_ids if p not in ctx.deps.passage_ids]
        if missing:
            raise ModelRetry(
                f"These ids are not in the provided pool: {missing}. "
                "Select only ids present in the context — do not invent."
            )
        return selection

    return agent


def _render_pool(summary: str, inv_pool: dict[str, dict[str, Any]], pool: RetrievalPool) -> str:
    """The Call 1b prompt: the baseline summary + the candidate pool the model
    selects from (invariant ids it may keep, passages, and any zoom results)."""
    lines = [summary, "", "CANDIDATE INVARIANTS (id — title [weight]):"]
    for iid, inv in inv_pool.items():
        lines.append(f"  {iid} — {inv.get('title', '')} [{inv.get('weight_effective', '?')}]")
    if pool.passages:
        lines.append("CANDIDATE PASSAGES (id — excerpt):")
        for p in pool.passages:
            lines.append(f"  {p['id']} — {str(p['excerpt'])[:160]}")
    for z in pool.zoom_results:
        lines.append(f"ZOOM {z['kind']}({z['arg']}): {len(z['rows'])} rows")
    return "\n".join(lines)


class PlannerPre:
    """Assembles the Worker's context for one UC8 cycle (docs/TASKS.md Task
    4.1). Agents are built once and held; `run` is called per cycle. Tests
    `.override(model=TestModel(...))` the two agents to exercise the
    orchestration without the transport."""

    def __init__(
        self,
        db: InvestmentDB,
        embedder: QueryEmbedder,
        model_name: str,
        api_key: str,
        *,
        reasoning_effort: str = PLANNER_REASONING_EFFORT,
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        self._db = db
        self._embedder = embedder
        self.query_agent = build_query_agent(
            model_name, api_key, reasoning_effort=reasoning_effort, base_url=base_url
        )
        self.context_agent = build_context_agent(
            model_name, api_key, reasoning_effort=reasoning_effort, base_url=base_url
        )

    @property
    def embedder(self) -> QueryEmbedder:
        """The shared embedder, exposed so the UC8 cycle can hand it to the
        knowledge commit (new_invariant dedup) without rebuilding one."""
        return self._embedder

    async def run(
        self, trigger: str, history: list[dict[str, Any]] | None = None
    ) -> tuple[PlannerContext, WorkerTools]:
        """(PlannerContext, tool_registry) — the Worker's context plus the three
        bridged tools bound to the db (their connection captured in the closure,
        never handed to the Worker). `history` is reserved for future framing;
        the baseline already carries `recent_proposals`."""
        baseline = await gather_baseline(self._db)
        summary = render_baseline_summary(baseline)

        query_result = await self.query_agent.run(f"Trigger: {trigger}\n\nBaseline:\n{summary}")
        queries = query_result.output

        vectors = self._embedder.encode(queries.corpus_queries)
        pool = await retrieve(self._db, vectors, queries.zooms)

        inv_pool, pas_pool = invariant_pool(baseline, pool), passage_pool(pool)
        deps = _Pools(frozenset(inv_pool), frozenset(pas_pool))
        selection_result = await self.context_agent.run(
            _render_pool(summary, inv_pool, pool), deps=deps
        )
        selection = selection_result.output

        regime_type = baseline.regime.get("regime_type_id")
        active = await active_invariant_ids(self._db, selection.invariant_ids, regime_type)
        context = assemble_context(baseline, pool, selection, active)
        return context, WorkerTools(self._db)
