"""Typed settings loaded from .env — see .env.example for the full key list.

Fails at import time (pydantic-settings) if a required key is missing,
per CLAUDE.md "Dev standards": before the scheduler starts, not
mid-way through the Monday chain.
"""

import os
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _expand_path(v: str) -> Path:
    return Path(os.path.expandvars(v))


ExpandedPath = Annotated[Path, BeforeValidator(_expand_path)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", frozen=True)

    # LLMs — BOTH roles now route through OpenRouter (owner decision,
    # 2026-07-21). CLAUDE.md still describes the Worker as "claude-sonnet-5 via
    # Anthropic"; the model is unchanged, only the transport is (OpenRouter's
    # id for it is `anthropic/claude-sonnet-5`). One provider means one key,
    # one client construction, and one place to compare models — which is what
    # makes the curator's cheap-vs-expensive A/B a config change rather than a
    # code change. Needs recording in the docs (see MILESTONES M7).
    #
    # `anthropic_api_key` is consequently OPTIONAL: nothing reads it while both
    # roles go through OpenRouter. Kept rather than deleted because ADR-007's
    # bridge philosophy applies here too — reverting the Worker to the direct
    # Anthropic transport should not require a schema change. It is the one
    # required-key exception to CLAUDE.md's "fails at startup on missing keys",
    # and it is deliberate: failing startup over a key no code path uses is
    # noise, not safety.
    anthropic_api_key: str | None = None
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    planner_model: str = "deepseek/deepseek-v4-flash"
    planner_thinking_budget_pre: int = 512
    planner_thinking_budget_post: int = 1024
    worker_model: str = "anthropic/claude-sonnet-5"
    # Reasoning depth for the curator (docs/TASKS.md Task 5.3). `high`, by owner
    # decision 2026-07-21 after the ice-core runs.
    #
    # Be precise about what was and was not established: at `high`, the curator
    # produces valid registry predicates and 100% of its candidates clear the
    # expressibility gate. `xhigh` was NEVER measured to completion — the first
    # attempt died on the tool-calling bug, the second was stopped. So this is
    # not "xhigh was tried and rejected"; it is "high demonstrably works and
    # xhigh is not worth the latency to explore". Cost was never the deciding
    # factor either way: the whole book runs for cents at any level.
    curator_reasoning_effort: str = "high"
    embedding_model: str = "all-MiniLM-L6-v2"

    # SQLite
    db_path: ExpandedPath

    # Scheduling
    tz: str = "Europe/Zurich"

    # Ingestion
    inbox_path: ExpandedPath
    sources_path: ExpandedPath

    # Market data — the fetch universe (tickers, sources, transforms, lags) and
    # the composite/derived-signal definitions live in db/seed_data.py
    # (ALLOWED_TICKERS is authoritative — TASKS.md Task 2.1: the fetcher is
    # "driven by the allowed_tickers documents"), NOT in .env.
    fred_api_key: str
    market_backfill_years: int = 35

    # Local ops
    local_api_port: int = 8765

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # User profile defaults (BINDING rules — see docs/REVISION_NOTES.md)
    user_currency: str = "CHF"
    # ADR-007: raised from -15 for the accumulation-horizon market-signal stack;
    # applies to the STACK's realized drawdown, not each book's standalone one.
    user_max_drawdown_pct: float = -25.0
    # ADR-007 addendum (2026-07-20): raised from 40 for the DELIBERATELY
    # concentrated market-signal books (the two credit-spread-* equity books hold
    # SPY 50, the tight-yield-curve-steep one holds VCIT 50)
    # — that concentration is the source of the +2.5-vs-B edge; the 40 cap was
    # calibrated for the diversified Dalio portfolios it replaces as the live path.
    user_max_single_asset_pct: float = 50.0
    user_benchmark: str = "all-weather-USD"
    user_phase: str = "accumulation"
    user_auto_validation_hours: int = 48
