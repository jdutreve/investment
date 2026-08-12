"""Typed settings loaded from .env — see .env.example for the full key list.

Fails at import time (pydantic-settings) if a required key is missing,
per CLAUDE.md "Dev standards": before the scheduler starts, not
mid-way through the weekly chain.

`.env` IS THE CONFIGURATION — see `settings_customise_sources` and `ENV_FILE`
below for the two ways that is enforced, and why.
"""

import os
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


def _expand_path(v: str) -> Path:
    return Path(os.path.expandvars(v))


ExpandedPath = Annotated[Path, BeforeValidator(_expand_path)]

# ABSOLUTE, resolved from this file rather than from the process CWD.
#
# `env_file=".env"` is CWD-relative, so the agent picked up its configuration
# only when launched from the repo root and silently fell back to defaults
# anywhere else — a scheduled job, a CLI run from another directory, a test
# harness. Anchoring it to the source tree makes "which .env" a property of the
# INSTALL, not of whoever started the process.
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", frozen=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """`.env` OUTRANKS the process environment. Highest priority first.

        pydantic-settings ships the opposite order (env > dotenv), which is
        right for a deployed service configured by its orchestrator and wrong
        for this one: `.env` is the single-user agent's configuration DOCUMENT,
        the file the owner edits and the only place the settings are written
        down. An exported shell variable is ambient state nobody wrote down.

        This is not theoretical. On 2026-08-05 the owner swapped both LLM models
        in `.env`; a shell exporting the previous values shadowed the file, and
        the agent kept running the old models while the file said otherwise. The
        edit had no effect and nothing reported that. Under the default order,
        the more authoritative the source LOOKS, the quieter it fails.

        `init_settings` stays on top so explicit constructor arguments still
        win — that is how the tests build a Settings without touching `.env`
        (`_env_file=None`), and an argument passed in code is as deliberate as
        an edit to the file.

        The cost, stated plainly: `FOO=x uv run ...` no longer overrides a key
        present in `.env`. For a local single-user agent that is the intended
        trade — reproducibility over ad-hoc override. Anything genuinely
        per-run belongs in a CLI flag, where it is visible in the command."""
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)

    # LLMs — EVERY role routes through OpenRouter (owner decision, 2026-07-21;
    # still NOT a numbered ADR, DECISIONS.md stops at ADR-011). One provider
    # means one key, one client construction, and one place to compare models —
    # which is what makes a cheap-vs-expensive A/B a `.env` edit rather than a
    # code change.
    #
    # `anthropic_api_key` is consequently OPTIONAL: nothing reads it while every
    # role goes through OpenRouter. Kept rather than deleted because ADR-007's
    # bridge philosophy applies here too — reverting a role to a direct
    # provider transport should not require a schema change. It is the one
    # required-key exception to CLAUDE.md's "fails at startup on missing keys",
    # and it is deliberate: failing startup over a key no code path uses is
    # noise, not safety.
    anthropic_api_key: str | None = None
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # NO DEFAULTS, deliberately — which model runs is `.env`'s business alone.
    #
    # A default model id is a hard-coded assumption about the most swappable
    # part of the system, and it rots in the worst way: silently. This file
    # carried defaults that the owner had already replaced in `.env`, and a run
    # falling back to one is indistinguishable from a run that was configured.
    # Required means pydantic-settings refuses to start — CLAUDE.md's own rule,
    # applied to the two keys that decide what the system thinks WITH.
    #
    # Nothing downstream may name a model either: the roles take `model_name`
    # as a parameter and the code states what it needs from a model (structured
    # output, function tools, a reasoning-effort knob), never which one supplies
    # it.
    planner_model: str
    worker_model: str
    planner_thinking_budget_pre: int = 512
    planner_thinking_budget_post: int = 1024
    # Reasoning depth for the curator (docs/TASKS.md Task 5.3). `high`, by owner
    # decision 2026-07-21 after the ice-core runs.
    #
    # Be precise about what was and was not established, and on WHICH model —
    # this is a measurement on the planner model of the day (2026-07-21), not a
    # property of the setting: at `high`, the curator produced valid registry
    # predicates and 100% of its candidates cleared the expressibility gate.
    # `xhigh` was NEVER measured to completion — the first attempt died on the
    # tool-calling bug, the second was stopped. So this is not "xhigh was tried
    # and rejected"; it is "high demonstrably worked and xhigh was not worth the
    # latency to explore". Cost was never the deciding factor either way: the
    # whole book runs for cents at any level. Re-measure after a model swap.
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
    # USD, not CHF (owner, 2026-08-08): everything in this system is USD —
    # display, NAV, performance. The CHF default was the last survivor of an
    # earlier assumption already retired elsewhere: `ratios.TRADING_COST_BPS`
    # records on 2026-08-02 that "every portfolio in this system is
    # USD-denominated and held in a USD account", and both `user_profile` and
    # all 113k `portfolio_nav` rows already said USD while the 12 `portfolio`
    # rows still said CHF. A label contradicting the data around it is the same
    # class of defect as the FAVORS drawdown aggregate: it misleads every
    # reader, including the Worker, which sees these rows.
    user_currency: str = "USD"
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
