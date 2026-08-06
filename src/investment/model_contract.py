"""Checks that the models named in `.env` can actually do what each role needs.

`config.py` deliberately hard-codes no model: `PLANNER_MODEL` and `WORKER_MODEL`
are required, nothing in the source names one, and each role states the
CAPABILITIES it requires rather than a name. That freedom has a price — a model
that cannot meet a role's contract fails at the first real cycle, not at
startup, and on the UC8 path "the first real cycle" is a Monday morning with the
whole chain behind it.

This is the cheap check that closes that gap, and the one `.env.example` tells
you to run after a swap:

    uv run python -m investment.model_contract

Two calls, cents, ~1-2 minutes. It exercises the PRODUCTION builders — the same
`build_query_agent` and `build_worker_agent` the Monday chain uses — so what
passes here is the path that will run, not a simplification of it.

THE CONTRACTS, one requirement per reported line:

  Planner   structured output (`QueryStrategies`) + a `reasoning_effort` the
            provider accepts.
  Worker    the same, plus FUNCTION TOOLS IN THE SAME RUN — it must call the
            bridged tools mid-reasoning and still return a valid `WorkerResult`.
            This is the requirement most likely to break on a swap (see
            worker/curator.py: reasoning models have rejected or mangled forced
            tool calls), so the tool call is checked explicitly rather than
            inferred from the run merely succeeding.

Read-only by construction: it runs against a throwaway COPY of the live DB, so
a Worker that decides to explore cannot touch the real one, and the live agent's
single-writer discipline (ADR-004) is never contended.
"""

import asyncio
import logging
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from pydantic_ai.messages import ToolCallPart
from pydantic_ai.usage import UsageLimits

from investment.config import Settings
from investment.db.sqlite import InvestmentDB
from investment.planner.pre import build_query_agent
from investment.worker.agent import (
    WORKER_REQUEST_LIMIT,
    WORKER_TOOL_CALLS_LIMIT,
    build_worker_agent,
)
from investment.worker.tools import WorkerTools

logger = logging.getLogger(__name__)

# Derived from the class rather than listed, so a fourth bridged tool is counted
# the day it is written instead of the day someone remembers this file.
BRIDGED_TOOL_NAMES = frozenset(name for name in vars(WorkerTools) if not name.startswith("_"))

# Small enough to be answerable, specific enough that a model cannot satisfy it
# without producing the real schema.
PLANNER_PROBE = (
    "Regime: falling-growth-rising-inflation, newly confirmed this week. Two "
    "proposals are pending judgement. Propose your corpus search strategies for "
    "this week's context assembly."
)

# `portfolio_check` is named EXPLICITLY. The point of this probe is to force the
# tool leg of the contract, and a probe the model can answer from the prose
# alone tests nothing — a swap would come back green while the capability that
# actually breaks went unexercised.
WORKER_PROBE = """\
MARKET-SIGNAL ALLOCATION (already decided mechanically this month):
  book = credit-spread-wide-defensive, holdings IEF 60 / IWN 40.

Call portfolio_check on 'ms-stack' first, so your reading rests on its actual
indicators rather than on memory. Then give your reading of the allocation above
in market_signal_assessment. Propose nothing else."""


@dataclass(frozen=True)
class CheckResult:
    """One role's verdict. `detail` is shown on success too — a contract that
    passes silently teaches nothing about what the model actually did."""

    role: str
    model: str
    passed: bool
    seconds: float
    detail: str


def copy_db(source: Path, dest: Path) -> None:
    """The live DB is WAL, so a file copy can catch a torn state — the backup
    API is the only safe snapshot (same reasoning as db/as_of_snapshot.py)."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()


async def check_planner(settings: Settings) -> CheckResult:
    started = monotonic()
    # Construction is INSIDE the try: a malformed model id or base_url can fail
    # before any call is made, and this tool exists to turn a bad `.env` into a
    # FAIL line — a traceback out of the checker is the failure mode it is
    # supposed to replace.
    try:
        agent = build_query_agent(
            settings.planner_model,
            settings.openrouter_api_key,
            reasoning_effort="high",
            base_url=settings.openrouter_base_url,
        )
        result = await agent.run(PLANNER_PROBE)
    except Exception as exc:  # a failed contract IS the finding, not a crash
        return CheckResult(
            "planner", settings.planner_model, False, monotonic() - started, _describe(exc)
        )
    out = result.output
    detail = f"{len(out.corpus_queries)} corpus queries, {len(out.zooms)} zooms"
    return CheckResult("planner", settings.planner_model, True, monotonic() - started, detail)


async def check_worker(settings: Settings, db_path: Path) -> CheckResult:
    db = InvestmentDB(db_path)
    started = monotonic()
    try:
        # Construction inside the try, for the reason given in `check_planner`.
        agent = build_worker_agent(db, settings.worker_model, settings.openrouter_api_key)
        # `agent.run` rather than `run_worker`, with the production limits
        # imported so the two cannot drift: this needs the message trace to
        # prove a tool was actually called, and `run_worker` deliberately
        # discards everything but the output.
        result = await agent.run(
            WORKER_PROBE,
            usage_limits=UsageLimits(
                request_limit=WORKER_REQUEST_LIMIT,
                tool_calls_limit=WORKER_TOOL_CALLS_LIMIT,
            ),
        )
    except Exception as exc:  # a failed contract IS the finding, not a crash
        return CheckResult(
            "worker", settings.worker_model, False, monotonic() - started, _describe(exc)
        )
    finally:
        await db.close()

    elapsed = monotonic() - started
    # FILTERED to the bridged tools, and that filter is the whole check.
    # In tool-output mode PydanticAI delivers the structured answer as a tool
    # call of its own (`final_result`), so "did it call any tool" is true for
    # every model that merely returns a valid object — the naive version of this
    # check passed the exact case it exists to catch.
    tools = sorted(
        {
            part.tool_name
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolCallPart) and part.tool_name in BRIDGED_TOOL_NAMES
        }
    )
    if not tools:
        # A valid WorkerResult with no tool call is a HALF-met contract, and the
        # missing half is the one UC8 depends on. Reported as a failure, not as
        # a warning: the run looked fine, which is exactly the problem.
        return CheckResult(
            "worker",
            settings.worker_model,
            False,
            elapsed,
            "returned a valid WorkerResult but never called a tool — the probe "
            "asked for portfolio_check explicitly, so this model does not do "
            "structured output and function tools in the same run",
        )
    assessment = result.output.market_signal_assessment or ""
    detail = f"tools called: {', '.join(tools)}; assessment {len(assessment)} chars"
    return CheckResult("worker", settings.worker_model, True, elapsed, detail)


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def render(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"[{mark}] {r.role:<8} {r.model:<34} {r.seconds:6.1f}s  {r.detail}")
    return "\n".join(lines)


async def run_checks(settings: Settings) -> list[CheckResult]:
    """Both contracts against a throwaway copy of the configured DB."""
    with tempfile.TemporaryDirectory(prefix="model-contract-") as scratch:
        snapshot = Path(scratch) / "snapshot.db"
        copy_db(settings.db_path, snapshot)
        # Sequential, not gathered: two lines of output arriving in order are
        # worth more here than the ~30s saved, and a planner failure is usually
        # the cheaper one to read first.
        return [await check_planner(settings), await check_worker(settings, snapshot)]


def _main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings()  # type: ignore[call-arg]  # pydantic-settings fills from .env
    results = asyncio.run(run_checks(settings))
    print(render(results))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(_main())
