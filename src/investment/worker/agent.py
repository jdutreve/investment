"""The Worker agent — the investment expert that interprets the prepared
context and proposes (docs/ARCHITECTURE.md "WORKER"; docs/TASKS.md Phase 5
"Worker agent"; docs/USE_CASES.md UC8).

One PydanticAI agent on WORKER_MODEL (whatever `.env` names — config.py owns
that choice and nothing here may assume it), output_type=`WorkerResult`, with
the three bridged tools registered (worker/tools.py). The Worker interprets
pre-computed indicators, never recalculates; it is UNAWARE of the Planner,
Writeback and storage — the system prompt says so, and the tools hand it data
without ever exposing the connection (worker/tools.py `WorkerTools`).

Same transport as the curator (config.py: every role routes through
OpenRouter), and the same two-knob robustness split: `max_retries` on the HTTP
client covers transport faults with backoff; PydanticAI's `retries` covers
schema-validation faults (the "Phase 1bis policy" — validate, retry once with
the error appended, then raise, never a silent pass).

WHAT THIS ROLE REQUIRES OF A MODEL — the contract a swap must satisfy, stated
here so a swap can be checked instead of guessed:

  1. structured output AND function tools in the same run. Unlike the curator
     (which only needs a final object, and overrides to native output because
     reasoning models mangled forced tool calls), the Worker must call the
     three tools MID-reasoning, so the bundled profile's default tool-mode
     output path is kept rather than overridden. A model that cannot do both
     at once needs its own profile override here, not a prompt change.
  2. a `reasoning_effort` knob the provider accepts (`WORKER_REASONING_EFFORT`).
  3. tolerance for being corrected: it will get a tool argument wrong, and
     `ToolInputError` is a `ModelRetry` precisely so that costs a turn instead
     of the cycle (worker/tools.py).

Requirement 1 used to be justified in this docstring by naming the model and
asserting it was fine. That reasoning does not survive a model swap — the
justification silently becomes false while the code keeps working, which is
the worst of both. State the requirement; let the smoke test state the model.
"""

import logging
from pathlib import Path

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import UsageLimits

from investment.db.sqlite import InvestmentDB
from investment.worker.result import WorkerResult
from investment.worker.tools import WorkerTools, describe_schema

logger = logging.getLogger(__name__)

# Same policy as the curator (worker/curator.py), local by the same reasoning:
# these are transport/validation knobs of THIS role, not shared config.
OUTPUT_RETRIES = 2  # PydanticAI schema-validation retries (Phase 1bis policy)
TRANSPORT_RETRIES = 2  # HTTP client retries with backoff (CLAUDE.md async standard)
WORKER_TIMEOUT_SECONDS = 300.0

# The agentic-loop budget. `OUTPUT_RETRIES`/`TRANSPORT_RETRIES` bound schema and
# transport faults; NEITHER bounds the tool loop, so before this a Worker that
# kept calling `db_query` ran until the 300s timeout, billing every turn — the
# one unbounded cost in a system whose whole point is that the mechanical half
# is free.
#
# The three bridged tools are already ROW-capped (worker/tools.py: 20/30 rows)
# but were not CALL-capped, and the two bound different things: rows bound what
# one answer may contain, calls bound how many questions may be asked. The
# Planner's margin has had `MAX_ZOOMS = 3` since M8 for exactly this reason
# (planner/retrieval.py); this is the Worker's equivalent.
#
# A RUNAWAY guard rather than a frugality knob — the Worker legitimately checks
# several portfolios and tickers before proposing, and a budget that bites in
# normal use costs a whole cycle to save pennies. `request_limit` sits above it
# because each tool call costs one request and the final structured answer costs
# one more, plus the retries.
#
# 12, RAISED FROM THE SPEC'S 8 (docs/TASKS.md Phase 5, "1-8 tool calls budget")
# by owner decision 2026-08-06, because the guard did exactly what its own
# rationale warned against. On the M8b run, the Worker asked for a 9th call at
# one decision date and a 10th on the retry, and the date was lost — not to a
# runaway loop but to ordinary thoroughness: it had checked the books, the
# tickers and the invariants it was about to cite.
#
# The 8 was never measured, it was assumed, and it was assumed against a
# different model. This one explores more per cycle, which is a property of the
# model and not a fault in it. Left at 8, the budget would silently cost the
# most careful dates — the ones whose reasoning is worth the most.
#
# Still a hard cap, and still the thing that stops an unbounded loop: 12 is
# generous for the deliberation and nowhere near a runaway. Re-derive it if the
# model changes again — it is a per-model number wearing a spec number's
# clothes.
WORKER_TOOL_CALLS_LIMIT = 12
WORKER_REQUEST_LIMIT = WORKER_TOOL_CALLS_LIMIT + OUTPUT_RETRIES + 2

# The UC8 allocation decision is the highest-stakes single call in the system;
# 'high' is the reasoning depth for it — the balance point, not a measured
# optimum, and overridable per call.
#
# Which levels a model accepts is the PROVIDER's business: OpenRouter validates
# the value and errors loudly on one it does not know, which is the behaviour
# we want on a model swap. Do not encode a per-model list here.
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
The ADOPTED allocation is the market-signal monthly stack: the book named in \
your context was ALREADY CHOSEN, mechanically, from market-priced credit-spread \
and yield-slope signals confronted over those same 35 years, with a 200-day \
trend overlay on top. Read it, challenge it, say plainly where it looks wrong \
and what it cannot see — that reading is your contribution. Do not re-pick the \
book: that decision is not yours, and a course set by a proven instrument is \
not improved by overruling it on impression. Neither its book NOR its weights \
are yours to adjust: proposing that allocation back with a few points moved is \
re-deciding it by another route. If you believe the instrument itself is wrong \
— not this month's reading, but the rule — say so as an innovation \
(strategy_revision), where the claim gets measured over time instead of \
applied once on conviction. A disagreement worth acting on is worth proving.
Evaluate strategies, rank portfolios, compare challengers against the \
defender, propose paper-mode adjustments. You may propose adjusting the \
defender's own allocation (blend 0.4 x active-scenario target + 0.6 x \
regime-favored structural anchor), citing the invariants that support it — \
this is the retained fallback book, kept as benchmark, not the adopted stack.
Use the Skills provided and the data in your context.
You are unaware of the Planner, Writeback, and internal storage.
Three tools: db_query, market_fetch, portfolio_check.
Sharpe/Sortino/Calmar are pre-calculated indicators in USD in the DB; the \
suffix is _rolling. Interpret them — do not recalculate.
Rolling window is 36 months. Risk-free rate is 3M T-Bill (^IRX).
WorkerResult must include innovations_proposed (empty list if none) and \
reallocation_proposed (null if none).
Your reading of the market-signal allocation goes in market_signal_assessment \
— always, even when you agree and even when you propose nothing else; it is \
recorded and shown to the owner. If your context carries no market-signal \
allocation, say so there in one line."""

# docs/TASKS.md Phase 5 names five skill files and specifies each one's
# contract; the persona above says "Use the Skills provided" and, until now,
# none existed — the prompt asserted a capability decomposition it did not ship.
# They live as MARKDOWN, not as Python string constants, for the reason they are
# separate files at all: a skill is a prompt fragment the owner edits and
# diffs, and burying the verdict contract inside a .py makes changing it a code
# change. Loaded once at import, sorted for a stable prompt (a prompt that
# reorders between runs invalidates provider-side caching and makes two runs
# incomparable).
SKILLS_DIR = Path(__file__).parent / "skills"

# EXPLICIT, because alphabetical order is not the order that matters. The
# Worker's only contribution to the ADOPTED allocation is its reading of the
# mechanical decision (ADR-011), so that skill leads; the knowledge factory —
# the part that survives the pivot unchanged and actually moves invariant
# weights — comes next; the retained bridge, which decides nothing, comes last.
# Sorted by filename this order was 3rd, 1st, 2nd, 4th.
#
# docs/TASKS.md Phase 5 named FIVE skills, one per capability. That list predates
# ADR-007: it has a skill for comparing challengers and none for reading the
# mechanical decision, because when it was written the ranked duel WAS the
# decision. Following it literally shipped guidance weighted toward the
# superseded path. The three bridge skills are merged into one file for the same
# reason — they are one job, keeping the fallback honest, and three files gave
# them three files' worth of the Worker's attention.
SKILL_ORDER = (
    "skill-read-market-signal.md",
    "skill-evaluate-strategy.md",
    "skill-interpret-invariants.md",
    "skill-the-retained-bridge.md",
)


def load_skills(directory: Path = SKILLS_DIR) -> str:
    """The skill files, concatenated in `SKILL_ORDER`.

    A file present but NOT listed is appended rather than dropped: a new skill
    that silently never reached the prompt would be the hardest kind of bug to
    see — everything runs, the guidance is simply absent.

    Missing directory or no files -> empty string, and the agent still runs on
    the persona alone. Deliberate: a missing skill file degrades the Worker's
    guidance, it does not make the cycle unsafe — every contract these describe
    is ALSO enforced mechanically (the verdict Literal, the allocation
    validators, gate 0, gate 6). The prompt teaches; the gates decide."""
    if not directory.is_dir():
        logger.warning("worker skills directory missing: %s", directory)
        return ""
    found = {path.name: path for path in directory.glob("*.md")}
    ordered = [found.pop(name) for name in SKILL_ORDER if name in found]
    for name in sorted(found):
        logger.warning("worker skill %s is not in SKILL_ORDER, appended last", name)
        ordered.append(found[name])
    parts = [path.read_text(encoding="utf-8").strip() for path in ordered]
    return "\n\n---\n\n".join(part for part in parts if part)


def build_system_prompt(skills: str | None = None) -> str:
    """Persona + skills. Split so a caller can override the skills (tests, and
    the Phase 9 prompt A/B) without rebuilding the persona."""
    body = load_skills() if skills is None else skills
    if not body:
        return WORKER_SYSTEM_PROMPT
    return f"{WORKER_SYSTEM_PROMPT}\n\n# SKILLS\n\n{body}"


def build_worker_agent(
    db: InvestmentDB,
    model_name: str,
    api_key: str,
    *,
    reasoning_effort: str = WORKER_REASONING_EFFORT,
    base_url: str = "https://openrouter.ai/api/v1",
    skills: str | None = None,
) -> Agent[None, WorkerResult]:
    """The Worker agent, built once over the process-singleton DB connection
    (ADR-004: one connection injected everywhere). `WorkerTools(db)` closes the
    connection into the three bound methods that become the agent's tools, so
    the Worker calls `db_query(stmt)` and never sees `_db` — the least-privilege
    boundary of worker/tools.py, unchanged.

    Deps type is `None`: the tools carry their own state (the closed-over db),
    so nothing flows through PydanticAI's dependency channel — which is exactly
    what keeps the Worker unaware of storage.

    `skills` overrides the loaded skill files (tests, prompt A/B); `None` loads
    `skills/`."""
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
    agent: Agent[None, WorkerResult] = Agent(
        model,
        output_type=WorkerResult,
        instructions=build_system_prompt(skills),
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

    @agent.instructions
    async def _queryable_schema() -> str:
        """The tables `db_query` can read, appended to the prompt at run time.

        A DYNAMIC instruction rather than a constant, because the schema is read
        from the database (`describe_schema`) and this builder is synchronous —
        and because a schema that regenerates each run cannot go stale against a
        migration. It costs one local query per cycle against a dozen LLM calls
        the Worker no longer spends discovering the same thing.

        Measured 2026-08-06: without it the Worker opened every cycle by
        enumerating `sqlite_master`, twice over to beat the 20-row cap, and
        exhausted budgets of 8 and then 12 tool calls before reaching a single
        market question. `describe_schema` carries the full account.

        It does NOT breach the Worker's unawareness of storage (worker/agent.py
        docstring): it already has `db_query` and is told the DB holds the
        indicators. Naming the tables tells it what it may ask, not who wrote
        them — the Planner, Writeback and the journal stay invisible
        (`SCHEMA_HIDDEN_TABLES`)."""
        return "# QUERYABLE TABLES (db_query)\n\n" + await describe_schema(db)

    return agent


async def run_worker(agent: Agent[None, WorkerResult], context: str) -> WorkerResult:
    """Run one UC8 cycle: hand the Worker its prepared context and let it call
    the tools until it produces a complete `WorkerResult`.

    `context` is the Planner's assembled prompt (M8 Planner slice); until that
    lands, callers pass the rendered baseline directly. On schema-validation
    exhaustion PydanticAI raises (the Phase-1bis "never a silent pass" rule);
    the Monday-chain abort + ErrorEvent wrapping is the chain assembler's job,
    not this function's.

    Bounded by `UsageLimits`: exceeding the budget raises `UsageLimitExceeded`,
    which surfaces exactly like a schema exhaustion — the chain aborts and alerts
    (CLAUDE.md: "unhandled errors surface"). Deliberately NOT caught into a
    degraded result: a Worker that burned 12 tool calls without answering has
    not produced a weaker opinion, it has failed, and a half-cycle silently
    written to the graph is worse than a Monday with no cognitive read."""
    result = await agent.run(
        context,
        usage_limits=UsageLimits(
            request_limit=WORKER_REQUEST_LIMIT,
            tool_calls_limit=WORKER_TOOL_CALLS_LIMIT,
        ),
    )
    return result.output
