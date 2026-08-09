"""M8 tests for `worker/tools.py` — the Worker's only DB access.

This is a SECURITY boundary whose caller is a language model, so the tests are
adversarial by design: what is pinned is what the tools REFUSE, and that the
refusal message tells the model enough to correct itself.
"""

import inspect
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelRetry

from investment.db.sqlite import InvestmentDB
from investment.worker.tools import (
    DB_QUERY_MAX_ROWS,
    MARKET_FETCH_MAX_ROWS,
    PORTFOLIO_EXPOSED_FIELDS,
    SCHEMA_HIDDEN_TABLES,
    ToolInputError,
    WorkerTools,
    describe_schema,
    validate_sql,
)


@pytest.fixture
async def tools(tmp_path: Path) -> AsyncIterator[WorkerTools]:
    db = InvestmentDB(tmp_path / "t.db")
    await db.command(
        "INSERT INTO allowed_tickers (ticker, asset_class, currency, source, transform, active) "
        "VALUES ('SPY', 'equities', 'USD', 'yahoo', 'none', 1)"
    )
    await db.command(
        "INSERT INTO allowed_tickers (ticker, asset_class, currency, source, transform, active) "
        "VALUES ('OLD', 'equities', 'USD', 'yahoo', 'none', 0)"
    )
    for day in range(1, 29):
        for ticker in ("SPY",):
            await db.command(
                "INSERT INTO market_data (ticker, asset_class, currency, ts, level, speed, "
                "acceleration) VALUES (:t, 'equities', 'USD', :ts, 100.0, 0.1, 0.01)",
                t=ticker,
                ts=f"2026-07-{day:02d}",
            )
    await db.create_vertex(
        "framework", {"id": "fw-test", "name": "T", "enabled": 1, "trace": "test"}
    )
    await db.create_vertex(
        "portfolio",
        {
            "id": "all-weather-usd",
            "name": "All Weather",
            "defender": 1,
            "enabled": 1,
            "allocation": {"SPY": 30.0},
            "benchmark": "all-weather-USD",
            "framework_id": "fw-test",
            "currency": "USD",
            "max_drawdown_rule": -25.0,
            "max_single_asset_pct": 50.0,
            "phase": "accumulation",
            "trace": "test",
        },
    )
    yield WorkerTools(db)
    await db.close()


# -- db_query: what it refuses ----------------------------------------------


@pytest.mark.parametrize(
    "stmt",
    [
        "DELETE FROM invariant",
        "UPDATE portfolio SET defender = 1",
        "INSERT INTO invariant (id) VALUES ('x')",
        "DROP TABLE portfolio",
        "ALTER TABLE portfolio ADD COLUMN x TEXT",
        # Not in the spec's blacklist, added deliberately: these reach outside
        # the single file ADR-004 defines as the database, or re-enable writes.
        "ATTACH DATABASE '/tmp/evil.db' AS evil",
        "PRAGMA writable_schema = ON",
        "VACUUM",
    ],
)
async def test_write_and_escape_verbs_are_refused(tools: WorkerTools, stmt: str) -> None:
    with pytest.raises(ToolInputError):
        await tools.db_query(stmt)


async def test_a_second_statement_cannot_ride_along(tools: WorkerTools) -> None:
    """The classic injection shape. A blacklist alone would catch this one;
    it would not catch every verb, which is why the single-statement rule
    exists as its own check."""
    with pytest.raises(ToolInputError, match="one statement per call"):
        await tools.db_query("SELECT 1; DROP TABLE portfolio")


def test_a_column_name_containing_a_keyword_is_not_refused() -> None:
    """`created_at` contains CREATE. Substring matching would reject ordinary
    SQL and teach the model to avoid legitimate column names for reasons it
    cannot see."""
    assert "created_at" in validate_sql("SELECT created_at FROM invariant")
    assert "updated_at" in validate_sql("SELECT updated_at FROM invariant")


def test_only_select_and_with_are_allowed() -> None:
    validate_sql("SELECT 1")
    validate_sql("WITH x AS (SELECT 1) SELECT * FROM x")
    with pytest.raises(ToolInputError, match="SELECT or WITH"):
        validate_sql("EXPLAIN SELECT 1")


def test_empty_query_is_refused() -> None:
    with pytest.raises(ToolInputError, match="empty"):
        validate_sql("   ")


# -- db_query: the row cap --------------------------------------------------


def test_a_missing_limit_is_injected() -> None:
    assert validate_sql("SELECT * FROM invariant").endswith(f"LIMIT {DB_QUERY_MAX_ROWS}")


def test_a_smaller_limit_is_honoured() -> None:
    assert validate_sql("SELECT * FROM invariant LIMIT 5").endswith("LIMIT 5")


def test_a_larger_limit_is_overridden_not_trusted() -> None:
    """The cap protects the WORKER's context window, so "the model asked for
    1000" is exactly the case that must not win."""
    out = validate_sql("SELECT * FROM invariant LIMIT 1000")
    assert out.endswith(f"LIMIT {DB_QUERY_MAX_ROWS}")
    assert "1000" not in out


async def test_the_cap_binds_on_real_rows(tools: WorkerTools) -> None:
    rows = await tools.db_query("SELECT * FROM market_data")
    assert len(rows) == DB_QUERY_MAX_ROWS


# -- market_fetch -----------------------------------------------------------


async def test_market_fetch_returns_the_documented_shape(tools: WorkerTools) -> None:
    rows = await tools.market_fetch(["SPY"], "1m")
    assert rows
    assert set(rows[0]) == {"ts", "ticker", "level", "speed", "acceleration"}


async def test_market_fetch_is_capped(tools: WorkerTools) -> None:
    rows = await tools.market_fetch(["SPY"], "5y")
    assert len(rows) <= MARKET_FETCH_MAX_ROWS


async def test_an_inactive_ticker_is_outside_the_universe(tools: WorkerTools) -> None:
    with pytest.raises(ToolInputError, match="OLD"):
        await tools.market_fetch(["OLD"], "1m")


async def test_an_unknown_ticker_is_named_in_the_refusal(tools: WorkerTools) -> None:
    """The Worker can only correct a ticker it is told is wrong."""
    with pytest.raises(ToolInputError, match="TSLA"):
        await tools.market_fetch(["SPY", "TSLA"], "1m")


async def test_an_unknown_period_lists_the_valid_ones(tools: WorkerTools) -> None:
    with pytest.raises(ToolInputError, match="1m"):
        await tools.market_fetch(["SPY"], "yesterday")


async def test_no_tickers_is_refused(tools: WorkerTools) -> None:
    with pytest.raises(ToolInputError, match="no tickers"):
        await tools.market_fetch([], "1m")


async def test_a_query_naming_an_unknown_column_is_handed_back_not_raised(
    tools: WorkerTools,
) -> None:
    """`validate_sql` checks shape, never meaning — a valid-looking SELECT over
    a column that does not exist only fails inside SQLite. Measured 2026-08-06:
    the Worker wrote `SELECT c FROM portfolio` and killed the cycle. The SQLite
    message must reach the model, which is the only actor able to fix it."""
    with pytest.raises(ModelRetry, match="no such column"):
        await tools.db_query("SELECT no_such_column_here FROM portfolio")


async def test_a_refusal_is_handed_back_to_the_model_not_raised_at_the_chain(
    tools: WorkerTools,
) -> None:
    """The refusal messages name what is allowed so the Worker can correct
    itself — but PydanticAI only hands back `ModelRetry`; anything else aborts
    the run. Measured 2026-08-05: a Worker asking for period '6mo' (one
    character from the valid '6m') killed the whole UC8 cycle.

    Also asserts the message SURVIVES onto the exception, since that text is
    the entire mechanism by which the model learns what to fix."""
    with pytest.raises(ModelRetry) as caught:
        await tools.market_fetch(["SPY"], "6mo")
    assert "6m" in str(caught.value)
    # Still an ordinary ValueError outside an agent loop (validate_sql and
    # friends are used directly in tests and would otherwise pass silently).
    assert isinstance(caught.value, ValueError)


# -- portfolio_check --------------------------------------------------------


async def test_portfolio_check_exposes_only_the_allowlist(tools: WorkerTools) -> None:
    """An allowlist, not a denylist: a column added to `portfolio` later must
    be opted IN, so anything new defaults to invisible."""
    row = await tools.portfolio_check("all-weather-usd")
    assert set(row) == set(PORTFOLIO_EXPOSED_FIELDS)
    assert "trace" not in row
    assert "created_at" not in row


async def test_a_malformed_portfolio_id_is_refused(tools: WorkerTools) -> None:
    for bad in ["../etc", "Portfolio", "x" * 60, "", "a b"]:
        with pytest.raises(ToolInputError, match="malformed"):
            await tools.portfolio_check(bad)


async def test_an_unknown_portfolio_returns_empty_not_an_error(tools: WorkerTools) -> None:
    """Absence is an ANSWER — "no such portfolio" is information the Worker
    can reason about, not a failure it must recover from."""
    assert await tools.portfolio_check("does-not-exist") == {}


# -- the queryable schema ---------------------------------------------------


async def test_the_schema_names_the_tables_the_worker_may_query(tools: WorkerTools) -> None:
    """Measured 2026-08-06: with no schema in context the Worker opened every
    cycle by enumerating `sqlite_master`, splitting it in two to beat the 20-row
    cap, and exhausted budgets of 8 then 12 tool calls before asking anything
    about markets. The budget was never the problem — a tool that does not say
    what can be queried forces the model to find out, on the same budget as the
    thinking."""
    schema = await describe_schema(tools._db)

    assert "portfolio(" in schema
    assert "allowed_tickers(" in schema
    # columns, not just table names: knowing `favors` exists does not tell the
    # Worker it can select `sortino_rolling` from it
    assert "sortino_rolling" in schema


async def test_the_schema_hides_what_the_worker_must_not_see(tools: WorkerTools) -> None:
    """The Worker is unaware of Writeback and the journal (worker/agent.py), and
    the owner's caps bind through the gates rather than through its reasoning.
    Listing those tables would be an invitation."""
    schema = await describe_schema(tools._db)

    for hidden in SCHEMA_HIDDEN_TABLES:
        assert f"{hidden}(" not in schema


async def test_a_refused_tool_call_is_visible_in_the_log(
    tools: WorkerTools, caplog: pytest.LogCaptureFixture
) -> None:
    """Measured 2026-08-09 at 2008-10-01 of the on-stack run: the tool budget
    reported 15 calls, the log carried 7.

    `validate_sql` raises before `db_query` reaches its `logger.info`, so a
    REFUSED call cost a turn against `tool_calls_limit` and left no trace. The
    budget was therefore impossible to reason about from the logs — a day was
    spent theorising about thoroughness and loops while eight invisible
    refusals were doing the spending.

    The refusal must still REACH the model unchanged (it is a `ModelRetry`, and
    the self-correction depends on it), so this pins both halves."""
    with (
        caplog.at_level(logging.INFO, logger="investment.worker.tools"),
        pytest.raises(ToolInputError),
    ):
        await tools.db_query("DELETE FROM invariant")

    logged = [r.getMessage() for r in caplog.records]
    assert any("db_query REFUSED" in m for m in logged)
    assert any("READ-ONLY" in m for m in logged)  # the reason, not merely "refused"


async def test_the_decorator_keeps_what_pydantic_ai_reads_off_the_tool(
    tools: WorkerTools,
) -> None:
    """PydanticAI builds each tool's schema from its NAME, DOCSTRING and
    ANNOTATIONS. Wrapping the three methods to log refusals must not rename
    them or empty their descriptions — the Worker would lose the contract it is
    told to satisfy, which is how a tool budget gets spent guessing."""
    assert tools.db_query.__name__ == "db_query"
    assert "READ-ONLY" in (tools.db_query.__doc__ or "")
    assert "20 rows" in (tools.db_query.__doc__ or "")
    assert inspect.signature(tools.db_query).parameters.keys() == {"stmt"}
