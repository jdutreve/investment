"""The Worker agent — the investment expert that interprets the prepared
context and proposes (docs/ARCHITECTURE.md "WORKER"; docs/TASKS.md Phase 5
"Worker agent"; docs/USE_CASES.md UC8).

One PydanticAI agent on WORKER_MODEL (`anthropic/claude-sonnet-5` via
OpenRouter — config.py), output_type=`WorkerResult`, with the three bridged
tools registered (worker/tools.py). The Worker interprets pre-computed
indicators, never recalculates; it is UNAWARE of the Planner, Writeback and
storage — the system prompt says so, and the tools hand it data without ever
exposing the connection (worker/tools.py `WorkerTools`).

Same transport as the curator (config.py: both roles route through OpenRouter),
and the same two-knob robustness split: `max_retries` on the HTTP client covers
transport faults with backoff; PydanticAI's `retries` covers schema-validation
faults (the "Phase 1bis policy" — validate, retry once with the error appended,
then raise, never a silent pass).

Unlike the curator (a reasoning model that mangled forced tool calls, forcing
the native-output override), the Worker is Claude: the bundled OpenRouter
profile's default tool-mode output is exactly right for it, and it must ALSO
call the three function tools mid-reasoning — so the default output path is
kept, not overridden.
"""

import logging

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from investment.db.sqlite import InvestmentDB
from investment.worker.result import WorkerResult
from investment.worker.tools import WorkerTools

logger = logging.getLogger(__name__)

# Same policy as the curator (worker/curator.py), local by the same reasoning:
# these are transport/validation knobs of THIS role, not shared config.
OUTPUT_RETRIES = 2  # PydanticAI schema-validation retries (Phase 1bis policy)
TRANSPORT_RETRIES = 2  # HTTP client retries with backoff (CLAUDE.md async standard)
WORKER_TIMEOUT_SECONDS = 300.0

# The UC8 allocation decision is the highest-stakes single call in the system;
# 'high' is the reasoning depth for it. sonnet-5 accepts the OpenRouter effort
# levels (worker/curator.py notes 'xhigh' is accepted too); 'high' is the
# balance point, not a measured optimum — overridable per call.
WORKER_REASONING_EFFORT = "high"

# Verbatim from docs/ARCHITECTURE.md "WORKER system prompt". The persona is
# load-bearing: it fixes the DESTINATION (Phase-1 accumulation, don't-lose
# first), frames indicators as WEATHER to anticipate on speed/acceleration and
# invariants as LIGHTHOUSES that orient but never order, and states the
# unawareness of Planner/Writeback/storage that the tool boundary enforces.
WORKER_SYSTEM_PROMPT = """\
You are the CAPTAIN of this ship — a long-term investment expert, Phase 1 \
accumulation. Your DESTINATION is fixed: build retirement capital over 15-20 \
years. Rule #1: don't lose. Rule #2: don't forget rule #1.
You read the WEATHER — the market: the current regime, global liquidity, \
volatility, and the level/speed/acceleration of every series (speed and \
acceleration tell you whether a storm is building or easing, so you \
ANTICIPATE, not merely react).
You steer by LIGHTHOUSES — the invariants in your context orient your \
reasoning, they do not give orders (see skill-interpret-invariants).
You carry 35 YEARS of a sailor's experience — every indicator, backtest, \
FAVORS edge and invariant weight you read was already confronted over \
1991-present (1994, 2000, 2008, 2020, 2022).
You chart the course; the owner's hand is on the wheel — V1 never \
auto-executes, and final safety gates are applied outside you.
Evaluate strategies, rank portfolios, compare challengers against the \
defender, propose paper-mode adjustments. You may propose adjusting the \
defender's own allocation (blend 0.4 x active-scenario target + 0.6 x \
regime-favored structural anchor), citing the invariants that support it.
Use the Skills provided and the data in your context.
You are unaware of the Planner, Writeback, and internal storage.
Three tools: db_query, market_fetch, portfolio_check.
Sharpe/Sortino/Calmar are pre-calculated indicators in USD in the DB; the \
suffix is _rolling. Interpret them — do not recalculate.
Rolling window is 36 months. Risk-free rate is 3M T-Bill (^IRX).
WorkerResult must include innovations_proposed (empty list if none) and \
reallocation_proposed (null if none)."""


def build_worker_agent(
    db: InvestmentDB,
    model_name: str,
    api_key: str,
    *,
    reasoning_effort: str = WORKER_REASONING_EFFORT,
    base_url: str = "https://openrouter.ai/api/v1",
) -> Agent[None, WorkerResult]:
    """The Worker agent, built once over the process-singleton DB connection
    (ADR-004: one connection injected everywhere). `WorkerTools(db)` closes the
    connection into the three bound methods that become the agent's tools, so
    the Worker calls `db_query(stmt)` and never sees `_db` — the least-privilege
    boundary of worker/tools.py, unchanged.

    Deps type is `None`: the tools carry their own state (the closed-over db),
    so nothing flows through PydanticAI's dependency channel — which is exactly
    what keeps the Worker unaware of storage."""
    provider = OpenRouterProvider(
        openai_client=AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=TRANSPORT_RETRIES,
            timeout=WORKER_TIMEOUT_SECONDS,
        )
    )
    model = OpenAIChatModel(model_name, provider=provider)
    tools = WorkerTools(db)
    return Agent(
        model,
        output_type=WorkerResult,
        instructions=WORKER_SYSTEM_PROMPT,
        # The three bridged tools as BOUND methods (worker/tools.py) — the
        # connection is captured in the closure, never passed as a deps object
        # the Worker could read.
        tools=[tools.db_query, tools.market_fetch, tools.portfolio_check],
        retries=OUTPUT_RETRIES,
        model_settings=OpenAIChatModelSettings(
            timeout=WORKER_TIMEOUT_SECONDS,
            openai_reasoning_effort=reasoning_effort,  # type: ignore[typeddict-item]
        ),
    )


async def run_worker(agent: Agent[None, WorkerResult], context: str) -> WorkerResult:
    """Run one UC8 cycle: hand the Worker its prepared context and let it call
    the tools until it produces a complete `WorkerResult`.

    `context` is the Planner's assembled prompt (M8 Planner slice); until that
    lands, callers pass the rendered baseline directly. On schema-validation
    exhaustion PydanticAI raises (the Phase-1bis "never a silent pass" rule);
    the Monday-chain abort + ErrorEvent wrapping is the chain assembler's job,
    not this function's."""
    result = await agent.run(context)
    return result.output
