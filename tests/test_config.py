"""Tests for `config.py` — the precedence rules that make `.env` authoritative.

These are not style preferences: on 2026-08-05 the owner swapped both LLM models
in `.env` and the agent kept running the previous ones, because a shell exported
the old values and pydantic-settings ranks the process environment above the
dotenv file. The edit had no effect and nothing said so. Every test here pins one
half of the fix so the failure cannot come back silently.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from investment.config import ENV_FILE, Settings

REQUIRED = """\
OPENROUTER_API_KEY=from-file
FRED_API_KEY=from-file
TELEGRAM_BOT_TOKEN=from-file
TELEGRAM_CHAT_ID=from-file
GMAIL_ADDRESS=from-file
GMAIL_APP_PASSWORD=from-file
DB_PATH=/tmp/x/investment.db
INBOX_PATH=/tmp/x/inbox
SOURCES_PATH=/tmp/x/sources
"""


def _write_env(tmp_path: Path, extra: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(REQUIRED + extra, encoding="utf-8")
    return env


def test_the_dotenv_file_outranks_an_exported_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE regression. A stale export must not shadow the configuration file."""
    monkeypatch.setenv("WORKER_MODEL", "stale/exported")
    monkeypatch.setenv("PLANNER_MODEL", "stale/exported")
    env = _write_env(tmp_path, "PLANNER_MODEL=file/planner\nWORKER_MODEL=file/worker\n")

    settings = Settings(_env_file=env)  # type: ignore[call-arg]

    assert settings.planner_model == "file/planner"
    assert settings.worker_model == "file/worker"


def test_an_exported_variable_still_fills_a_key_the_file_omits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reordering the sources must not DISABLE the environment — it only
    demotes it. A key absent from the file still resolves from the process."""
    monkeypatch.setenv("WORKER_MODEL", "exported/worker")
    env = _write_env(tmp_path, "PLANNER_MODEL=file/planner\n")

    settings = Settings(_env_file=env)  # type: ignore[call-arg]

    assert settings.worker_model == "exported/worker"


def test_an_explicit_argument_outranks_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`init_settings` stays on top: code that passes a value said so on
    purpose, and this is how the test suite builds a Settings at all."""
    monkeypatch.setenv("WORKER_MODEL", "exported/worker")
    env = _write_env(tmp_path, "WORKER_MODEL=file/worker\n")

    settings = Settings(_env_file=env, planner_model="arg/planner", worker_model="arg/worker")  # type: ignore[call-arg]

    assert settings.worker_model == "arg/worker"


def test_a_missing_model_fails_at_startup_rather_than_defaulting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No default model id anywhere: an unconfigured run must be impossible to
    confuse with a configured one (CLAUDE.md — fail at startup, not mid-chain)."""
    monkeypatch.delenv("PLANNER_MODEL", raising=False)
    monkeypatch.delenv("WORKER_MODEL", raising=False)
    env = _write_env(tmp_path, "")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=env)  # type: ignore[call-arg]

    message = str(caught.value)
    assert "planner_model" in message
    assert "worker_model" in message


def test_the_env_file_is_found_independently_of_the_working_directory() -> None:
    """`env_file=".env"` was CWD-relative, so a job launched from elsewhere
    silently got no configuration. The path is anchored to the source tree."""
    assert ENV_FILE.is_absolute()
    assert ENV_FILE.name == ".env"
    assert (ENV_FILE.parent / "pyproject.toml").exists()
