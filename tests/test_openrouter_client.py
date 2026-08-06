"""Tests for the shared LLM transport (`investment/openrouter_client.py`).

Offline: what is pinned is the CONFIGURATION, because the bug was a
configuration that looked right. `timeout=300.0` reads like "give the model
five minutes" and also says "wait five minutes to open a socket" and "wait five
minutes for a pool slot" — the two nobody intended, and the two that turned a
dead keep-alive connection into a 497-second stall (measured 2026-08-06, with
OpenRouter's own record showing 1.3s for the same request).
"""

import httpx
import pytest

from investment.openrouter_client import (
    CONNECT_TIMEOUT_SECONDS,
    KEEPALIVE_EXPIRY_SECONDS,
    POOL_TIMEOUT_SECONDS,
    build_openrouter_client,
)


@pytest.fixture
def client() -> httpx.AsyncClient:
    built = build_openrouter_client(
        "sk-test", read_timeout=300.0, base_url="https://openrouter.ai/api/v1", max_retries=2
    )
    return built._client  # type: ignore[no-any-return]  # the openai SDK's httpx client


def test_the_phases_are_bounded_separately_not_by_one_float(
    client: httpx.AsyncClient,
) -> None:
    """THE regression. Connect and pool must not inherit the read budget: a
    socket that cannot be opened has to fail in seconds, while a model that is
    thinking is allowed its minutes."""
    timeout = client.timeout

    assert timeout.read == 300.0
    assert timeout.connect == CONNECT_TIMEOUT_SECONDS
    assert timeout.pool == POOL_TIMEOUT_SECONDS
    assert timeout.connect < timeout.read  # type: ignore[operator]


def test_an_idle_connection_is_dropped_before_the_network_drops_it(
    client: httpx.AsyncClient,
) -> None:
    """The actual fix. A decision cycle leaves minutes between calls; behind a
    home NAT an idle connection is reaped silently and httpx reuses it anyway,
    writing into a socket nobody is listening to. Closing it OURSELVES first
    costs a 200ms reconnect instead of a full read timeout of silence."""
    pool = client._transport._pool  # type: ignore[attr-defined]

    assert pool._keepalive_expiry == KEEPALIVE_EXPIRY_SECONDS
    # ...and short enough to beat the reaping it exists to pre-empt
    assert KEEPALIVE_EXPIRY_SECONDS <= 60.0


def test_each_role_keeps_its_own_patience_for_a_thinking_model() -> None:
    """Only `read` is per-role — the Worker deliberates far longer than the
    Planner's guardrail call, and nothing else about the transport differs."""
    planner = build_openrouter_client(
        "sk-test", read_timeout=60.0, base_url="https://x/v1", max_retries=1
    )
    worker = build_openrouter_client(
        "sk-test", read_timeout=300.0, base_url="https://x/v1", max_retries=2
    )

    assert planner._client.timeout.read == 60.0
    assert worker._client.timeout.read == 300.0
    assert planner._client.timeout.connect == worker._client.timeout.connect
