"""M8 tests for `worker/tools.py` — the Worker's only DB access.

This is a SECURITY boundary whose caller is a language model, so the tests are
adversarial by design: what is pinned is what the tools REFUSE, and that the
refusal message tells the model enough to correct itself.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.worker.tools import (
    DB_QUERY_MAX_ROWS,
    MARKET_FETCH_MAX_ROWS,
    PORTFOLIO_EXPOSED_FIELDS,
    ToolInputError,
    WorkerTools,
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
