"""ONE place that builds the HTTP client every LLM role talks through.

The four roles (Planner pre/post, Worker, curator) each constructed their own
`AsyncOpenAI` with `timeout=<a single float>`. That looks like four copies of a
harmless line and is in fact four copies of a bug.

WHAT A BARE FLOAT MEANS. httpx expands it to that value on ALL FOUR phases —
connect, read, write and pool. So a 300s "request timeout" also says: wait up
to five minutes to open a socket, and up to five minutes for a free slot in the
pool. Those are not the same promise, and only one of them was intended.

WHAT IT COST, measured on the M8b run of 2026-08-06. A decision cycle leaves
minutes between calls while the Worker deliberates. Behind a home NAT an idle
connection is reaped silently after two to five minutes, and httpx cheerfully
reuses it: the next request is written into a socket nobody is listening to.
No error, no reset — just silence until the read timeout fires. Measured at one
decision date: 497 seconds between two calls, of which OpenRouter's own record
accounts for 1.3s (one attempt, no fallback, no retry), and the process burned
11 seconds of CPU across 39 minutes. It was not thinking and it was not the
model. It was waiting on a dead pipe, for exactly `WORKER_TIMEOUT_SECONDS`.

Earlier stalls of 12 and 18 minutes were blamed on the laptop's lid closing
(ADR-002 — this machine sleeps). Sleep makes it worse; it is not the cause. The
cause was here.

THE FIX IS THE KEEPALIVE, not the timeouts. `keepalive_expiry` drops an idle
connection before the network does, so the next call opens a fresh one — 200ms,
against five minutes of writing into a corpse. The split timeouts are the
safety net that bounds the damage if it happens anyway: a connection that
cannot be opened fails in seconds instead of pretending to work for minutes.
Read stays long, because a model IS allowed to think.
"""

from typing import cast

import httpx
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openrouter import OpenRouterProvider

# Shorter than the shortest NAT/idle reaping we should assume, so WE close the
# connection first. A cycle's calls are seconds apart when the model is working;
# the multi-minute idle windows are between cycles, which is exactly when this
# should bite.
KEEPALIVE_EXPIRY_SECONDS = 20.0

# Opening a TCP+TLS connection to OpenRouter takes ~200ms. Ten seconds is a
# generous failure bound, not a budget — anything slower is broken, and finding
# out in ten seconds is the entire point.
CONNECT_TIMEOUT_SECONDS = 10.0
WRITE_TIMEOUT_SECONDS = 30.0
# Acquiring a slot from the pool is local bookkeeping; it should never take
# seconds, and waiting minutes for one hides the exhaustion rather than
# reporting it.
POOL_TIMEOUT_SECONDS = 10.0


def build_openrouter_client(
    api_key: str, *, read_timeout: float, base_url: str, max_retries: int
) -> AsyncOpenAI:
    """The transport for one role. `read_timeout` is the role's own patience
    with a model that is thinking (300s for the Worker, less for the Planner);
    every other phase is bounded here and identically for all of them, because
    none of them has a reason to differ."""
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=max_retries,
        timeout=httpx.Timeout(
            read_timeout,
            connect=CONNECT_TIMEOUT_SECONDS,
            write=WRITE_TIMEOUT_SECONDS,
            pool=POOL_TIMEOUT_SECONDS,
        ),
        http_client=httpx.AsyncClient(
            limits=httpx.Limits(keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS),
            timeout=httpx.Timeout(
                read_timeout,
                connect=CONNECT_TIMEOUT_SECONDS,
                write=WRITE_TIMEOUT_SECONDS,
                pool=POOL_TIMEOUT_SECONDS,
            ),
        ),
    )


def build_native_output_model(
    model_name: str,
    api_key: str,
    *,
    read_timeout: float,
    base_url: str = "https://openrouter.ai/api/v1",
    max_retries: int = 2,
) -> OpenAIChatModel:
    """An OpenRouter model configured for NATIVE structured output
    (`response_format: json_schema`) instead of PydanticAI's default
    tool-calling path.

    FOR ROLES THAT NEED A FINAL OBJECT AND NO FUNCTION TOOLS — the curator and
    the UC3 event triage. The Worker deliberately does NOT use this: it must
    call its three bridged tools mid-reasoning, so it keeps the bundled
    profile's tool-mode path (worker/agent.py states that requirement).

    WHY THE OVERRIDE. PydanticAI's bundled profile for these routes declares
    `supports_json_schema_output=False` and defaults to `tool`. It is stale, and
    trusting it produced real failures: reasoning models reject or mangle forced
    tool calls — Qwen errors outright ("tool_choice does not support being set
    to required in thinking mode") and DeepSeek silently emits an unparseable
    call, which surfaced as 3 of 10 ice-core batches exhausting their retries
    (2026-07-21). The same schema over the native path answered in 0.85s in a
    direct HTTP check. That measurement is provenance for the override, not a
    claim about whatever `.env` names today — a model that genuinely lacks the
    capability errors loudly rather than degrading silently, which is the
    failure mode to prefer on a swap.

    Lifted out of `curator.build_agent` when the event triage became the second
    role needing exactly this model (CLAUDE.md: "WHEN A SECOND ONE ARRIVES").
    """
    provider = OpenRouterProvider(
        openai_client=build_openrouter_client(
            api_key, read_timeout=read_timeout, base_url=base_url, max_retries=max_retries
        )
    )
    # The profile is a TypedDict at runtime: copy it and flip the two flags.
    base = dict(OpenAIChatModel(model_name, provider=provider).profile)
    base.update(supports_json_schema_output=True, default_structured_output_mode="native")
    return OpenAIChatModel(model_name, provider=provider, profile=cast(OpenAIModelProfile, base))
