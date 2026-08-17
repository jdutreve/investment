"""Tests for `model_contract.py` — the post-swap smoke check.

Offline by construction (`TestModel`): what is pinned here is the CHECKER's own
logic, not any model's behaviour. The one asymmetry worth stating: the checker
must fail a run that returns a perfectly valid `WorkerResult` without ever
calling a tool, because that is the half-met contract UC8 breaks on and the one
that looks like success from every other angle.
"""

import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from investment.config import Settings
from investment.db.sqlite import InvestmentDB
from investment.model_contract import (
    CheckResult,
    check_planner,
    check_worker,
    copy_db,
    render,
)
from investment.planner import pre as planner_pre
from investment.worker import agent as worker_agent

WORKER_OUTPUT = {
    "regime_assessment": "stagflation deepening",
    "ranking_commentary": "defender leads",
    "market_signal_assessment": "The defensive book fits the confirmed regime.",
    "scenario_adjustments": [],
    "evaluations": [],
    "innovations_proposed": [],
    "reasoning": "the book is defensible on its stated inputs",
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key="test",
        planner_model="test/planner",
        worker_model="test/worker",
        fred_api_key="test",
        telegram_bot_token="test",
        telegram_chat_id="test",
        gmail_address="test",
        gmail_app_password="test",
        db_path=tmp_path / "live.db",
        inbox_path=tmp_path / "inbox",
        sources_path=tmp_path / "sources",
    )  # type: ignore[call-arg]


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    """A real, empty schema — the Worker's tools need somewhere to look."""
    path = tmp_path / "probe.db"
    db = InvestmentDB(path)
    await db.close()
    return path


async def test_the_planner_contract_passes_on_a_structured_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = TestModel(custom_output_args={"corpus_queries": ["regime playbook"], "zooms": []})
    real = planner_pre.build_query_agent

    def _built(*args: object, **kwargs: object) -> object:
        agent = real(*args, **kwargs)  # type: ignore[arg-type]
        agent.model = model
        return agent

    monkeypatch.setattr(planner_pre, "build_query_agent", _built)
    monkeypatch.setattr("investment.model_contract.build_query_agent", _built)

    result = await check_planner(_settings(tmp_path))

    assert result.passed
    assert "1 corpus queries" in result.detail


async def test_a_worker_that_never_calls_a_tool_fails_the_contract(
    tmp_path: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE point of this module. `call_tools=[]` produces a valid WorkerResult
    from a model that cannot do tools — indistinguishable from success unless
    the tool call is checked explicitly.

    And it must be checked against the BRIDGED tools specifically: in tool-output
    mode the structured answer arrives as a `final_result` tool call, so a naive
    "any tool was called" test passes this exact case."""
    model = TestModel(call_tools=[], custom_output_args=WORKER_OUTPUT)
    real = worker_agent.build_worker_agent

    def _built(*args: object, **kwargs: object) -> object:
        agent = real(*args, **kwargs)  # type: ignore[arg-type]
        agent.model = model
        return agent

    monkeypatch.setattr("investment.model_contract.build_worker_agent", _built)

    result = await check_worker(_settings(tmp_path), db_path)

    assert not result.passed
    assert "never called a tool" in result.detail


async def test_a_worker_that_calls_a_tool_passes(
    tmp_path: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = TestModel(call_tools=["portfolio_check"], custom_output_args=WORKER_OUTPUT)
    real = worker_agent.build_worker_agent

    def _built(*args: object, **kwargs: object) -> object:
        agent = real(*args, **kwargs)  # type: ignore[arg-type]
        agent.model = model
        return agent

    monkeypatch.setattr("investment.model_contract.build_worker_agent", _built)

    result = await check_worker(_settings(tmp_path), db_path)

    assert result.passed
    assert "portfolio_check" in result.detail


async def test_a_provider_error_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected model id is the most likely outcome of a typo in `.env`, and
    it must read as a FAIL line naming the cause — not as a traceback."""

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("model not found: test/planner")

    monkeypatch.setattr("investment.model_contract.build_query_agent", _explode)

    result = await check_planner(_settings(tmp_path))

    assert not result.passed
    assert "model not found" in result.detail


def test_the_snapshot_is_a_usable_copy(tmp_path: Path) -> None:
    """Read-only against the live DB: the checker copies before it runs, so a
    Worker exploring during the probe cannot reach the real file."""
    source = tmp_path / "source.db"
    con = sqlite3.connect(source)
    with con:
        con.execute("CREATE TABLE t (x INTEGER)")
        con.execute("INSERT INTO t VALUES (42)")
    con.close()

    dest = tmp_path / "copy.db"
    copy_db(source, dest)

    copied = sqlite3.connect(dest)
    assert copied.execute("SELECT x FROM t").fetchone()[0] == 42
    copied.close()


def test_the_report_shows_both_verdicts_and_their_detail() -> None:
    text = render(
        [
            CheckResult("planner", "a/b", True, 9.2, "3 corpus queries, 1 zooms"),
            CheckResult("worker", "c/d", False, 6.9, "ToolInputError: unknown period"),
        ]
    )
    assert "[PASS] planner" in text
    assert "[FAIL] worker" in text
    assert "unknown period" in text
