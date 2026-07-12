"""Typed settings loaded from .env — see .env.example for the full key list.

Fails at import time (pydantic-settings) if a required key is missing,
per CLAUDE.md "Startup validation": before the scheduler starts, not
mid-way through the Monday chain.
"""

import os
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _expand_path(v: str) -> Path:
    return Path(os.path.expandvars(v))


def _split_csv(v: str) -> list[str]:
    return [item.strip() for item in v.split(",")]


ExpandedPath = Annotated[Path, BeforeValidator(_expand_path)]
CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", frozen=True
    )

    # LLMs
    anthropic_api_key: str
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    planner_model: str = "qwen/qwen3-8b"
    planner_thinking_budget_pre: int = 512
    planner_thinking_budget_post: int = 1024
    worker_model: str = "claude-sonnet-5"
    embedding_model: str = "all-MiniLM-L6-v2"

    # SQLite
    db_path: ExpandedPath

    # Scheduling
    tz: str = "Europe/Zurich"

    # Ingestion
    inbox_path: ExpandedPath
    sources_path: ExpandedPath

    # Market data
    fred_api_key: str
    market_backfill_years: int = 35
    yahoo_finance_tickers: CsvList
    fred_series: CsvList
    growth_composite_components: CsvList
    global_liquidity_components: CsvList
    real_rate_components: CsvList

    # Local ops
    local_api_port: int = 8765

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # User profile defaults (BINDING rules — see docs/REVISION_NOTES.md)
    user_currency: str = "CHF"
    user_max_drawdown_pct: float = -15.0
    user_max_single_asset_pct: float = 40.0
    user_benchmark: str = "all-weather-USD"
    user_phase: str = "accumulation"
    user_auto_validation_hours: int = 48
