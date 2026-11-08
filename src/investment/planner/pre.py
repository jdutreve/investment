"""Planner Pre — the two LLM calls that wrap the mechanical margin
(docs/ARCHITECTURE.md "Detailed Planner Steps"; docs/TASKS.md Task 4.1). This is
now thin: baseline (baseline.py), retrieval (retrieval.py) and context assembly
(context.py) are all built; `pre.py` only orchestrates them behind Call 1a and
Call 1b.

Flow (docs/ARCHITECTURE.md):
  PYTHON baseline → summary → CALL 1a (QueryStrategies, the VARIABLE margin) →
  PYTHON embed queries + retrieve + zooms → CALL 1b (ContextSelection) →
  PYTHON active-now + assemble → PlannerContext.

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
import re
from typing import Any, Protocol

import numpy as np
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from investment.db.sqlite import InvestmentDB
from investment.mechanical.invariants import evidence_summaries
from investment.openrouter_client import build_openrouter_client
from investment.planner.baseline import gather_baseline
from investment.planner.context import (
    ContextSelection,
    PlannerContext,
    active_invariant_ids,
    assemble_context,
    invariant_pool,
    passage_pool,
    render_baseline_summary,
    standing_label,
)
from investment.planner.retrieval import QueryStrategies, RetrievalPool, retrieve
from investment.telegram.digest import owner_view

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
  outcomes); prioritise invariants whose condition holds TODAY (marked
  ACTIVE), then those whose weight moved this week; dedupe. A DORMANT one —
  its condition does not hold — earns a place only when the week's question is
  whether that condition is about to hold.
- passage_ids: the passages worth showing, from the pool only. Two passages
  that say the same thing are one passage.
- notes: your framing of the week in a few sentences — the tension the expert
  should weigh, and what changed. NOT a list of the invariants or passages you
  selected: the expert reads them in full, so name none of their ids. The
  regime's figure is a signal strength out of 100, never a confidence or a
  probability. Call a condition active only if it is marked ACTIVE.

NEVER invent an id. Selecting an id absent from the pool will be rejected and
you will be asked to correct it."""


def _model(model_name: str, api_key: str, base_url: str) -> OpenAIChatModel:
    provider = OpenRouterProvider(
        # Shared transport (investment/openrouter_client.py): the split
        # timeouts and the bounded keepalive live in ONE place, because a
        # bare float here silently set the connect and pool budgets to the
        # read budget, and a reused dead connection then cost minutes.
        openai_client=build_openrouter_client(
            api_key,
            read_timeout=PLANNER_TIMEOUT_SECONDS,
            base_url=base_url,
            max_retries=TRANSPORT_RETRIES,
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


# The coach's framing, bounded: a few sentences, which the 2026-11-08 framing
# paragraph was (about a thousand characters) before its per-id listing.
NOTES_MAX_CHARS = 1200
CONFIDENCE_WORDING = re.compile(r"\d+(?:\.\d+)?\s*%\s*conf|\bconfidence\b", re.I)


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
        # THE NOTES ARE THE FRAMING, NOT A THIRD LISTING (2026-11-08): they
        # re-glossed all fourteen selected invariants and every passage the
        # Worker reads in full two sections above, called the regime's signal
        # strength "56.6% conf" and dormant conditions "most directly triggered".
        named = [
            i for i in (*ctx.deps.invariant_ids, *ctx.deps.passage_ids) if i in selection.notes
        ]
        problems = []
        if named:
            problems.append(
                f"they name pool ids {named[:5]}; the expert reads the selection itself"
            )
        if CONFIDENCE_WORDING.search(selection.notes):
            problems.append("they call the regime's signal strength a confidence")
        if len(selection.notes) > NOTES_MAX_CHARS:
            problems.append(f"they run past {NOTES_MAX_CHARS} characters")
        if not problems:
            return selection
        # COSMETIC, SO NEVER FATAL: the notes are framing, the selection is the
        # context. On the last attempt they are dropped rather than raised,
        # because an exhausted retry here ends the whole cognitive cycle — the
        # week's reading lost over a word in a paragraph.
        if ctx.retry >= ctx.max_retries:
            logger.warning("Call 1b notes dropped after %d retries: %s", ctx.retry, problems)
            return selection.model_copy(update={"notes": ""})
        raise ModelRetry(
            "Rewrite `notes` as the week's framing in a few sentences: " + "; ".join(problems)
        )

    return agent


def _render_pool(
    summary: str, inv_pool: dict[str, dict[str, Any]], pool: RetrievalPool, active: set[str]
) -> str:
    """The Call 1b prompt: the baseline summary + the candidate pool the model
    selects from (invariant ids it may keep, passages, and any zoom results).
    Each invariant says whether its condition holds today, which the coach
    could not know and guessed at until 2026-11-08 (twelve of fourteen chosen
    were dormant, presented as "triggered this week")."""
    lines = [summary, "", "CANDIDATE INVARIANTS (id — title [standing; ACTIVE or DORMANT]):"]
    for iid, inv in inv_pool.items():
        lit = "ACTIVE" if iid in active else "DORMANT"
        lines.append(f"  {iid} — {inv.get('title', '')} [{standing_label(inv)}; {lit}]")
    if pool.passages:
        lines.append("CANDIDATE PASSAGES (id — excerpt):")
        for p in pool.passages:
            lines.append(f"  {p['id']} — [{p.get('author') or '?'}] {str(p['excerpt'])[:160]}")
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
    ) -> PlannerContext:
        """The Worker's context for this cycle. The three bridged tools are NOT
        returned here — `worker/agent.py` binds its own `WorkerTools(db)` into
        the agent at build time, so a second registry would be dead weight.
        `history` is reserved for future framing; the baseline already carries
        `recent_proposals`."""
        baseline = await gather_baseline(self._db)
        summary = render_baseline_summary(baseline)

        query_result = await self.query_agent.run(f"Trigger: {trigger}\n\nBaseline:\n{summary}")
        queries = query_result.output

        vectors = self._embedder.encode(queries.corpus_queries)
        pool = await retrieve(self._db, vectors, queries.zooms)

        inv_pool, pas_pool = invariant_pool(baseline, pool), passage_pool(pool)
        deps = _Pools(frozenset(inv_pool), frozenset(pas_pool))
        regime_type = baseline.regime.get("regime_type_id")
        # Read for the whole pool BEFORE the selection, so the coach chooses
        # knowing which conditions hold today; the selection's subset follows.
        pool_active = await active_invariant_ids(self._db, list(inv_pool), regime_type)
        selection_result = await self.context_agent.run(
            _render_pool(summary, inv_pool, pool, pool_active), deps=deps
        )
        selection = selection_result.output

        active = pool_active & set(selection.invariant_ids)
        evidence = await evidence_summaries(self._db, selection.invariant_ids)
        context = assemble_context(baseline, pool, selection, active, queries, evidence)
        return dataclasses.replace(context, owner_view=await owner_view(self._db))
