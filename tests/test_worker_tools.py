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

from investment.db.seed_data import NON_PRICE_ASSET_CLASSES
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


async def _price(
    tools: WorkerTools,
    ticker: str,
    *,
    level: float,
    speed: float,
    acceleration: float,
    asset_class: str = "equities",
) -> None:
    """One allowed ticker with one recent row — enough for the shape assertions
    below, which are about the row a fetch returns, not about a series."""
    db = tools._db  # the fixture owns the DB; the tool only wraps it
    await db.command(
        "INSERT INTO allowed_tickers (ticker, asset_class, currency, source, transform, active) "
        "VALUES (:t, :c, 'USD', 'yahoo', 'none', 1)",
        t=ticker,
        c=asset_class,
    )
    await db.command(
        "INSERT INTO market_data (ticker, asset_class, currency, ts, level, speed, acceleration) "
        "VALUES (:t, :c, 'USD', '2026-07-28', :l, :s, :a)",
        t=ticker,
        c=asset_class,
        l=level,
        s=speed,
        a=acceleration,
    )


async def test_market_fetch_returns_the_documented_shape(tools: WorkerTools) -> None:
    rows = await tools.market_fetch(["SPY"], "1m")
    assert rows
    # A price row: the raw pair the DB stores, plus the comparable pair. No
    # `asset_class` — it selects the normalisation, it is not an answer.
    assert set(rows[0]) == {
        "ts",
        "ticker",
        "level",
        "speed",
        "acceleration",
        "speed_pct",
        "acceleration_pct",
    }


async def test_price_momentum_is_comparable_across_tickers(tools: WorkerTools) -> None:
    """Measured 2026-08-23, from a real Worker reading: "the strongest momentum
    in the cross-section is gold (GLD accel +1465)", set against IEF's +3.4.
    Both figures were correct and the comparison was meaningless — GLD trades
    near 12,098 and IEF near 502, so a raw second difference on a price mostly
    measures the price. Same true move, two levels: the raw numbers differ 24x,
    the normalised ones are equal."""
    await _price(tools, "BIG", level=12000.0, speed=1200.0, acceleration=600.0)
    await _price(tools, "SMALL", level=500.0, speed=50.0, acceleration=25.0)
    rows = {r["ticker"]: r for r in await tools.market_fetch(["BIG", "SMALL"], "1m")}
    assert rows["BIG"]["acceleration"] / rows["SMALL"]["acceleration"] == 24.0
    assert rows["BIG"]["acceleration_pct"] == rows["SMALL"]["acceleration_pct"] == 5.0
    assert rows["BIG"]["speed_pct"] == rows["SMALL"]["speed_pct"] == 10.0


@pytest.mark.parametrize("asset_class", sorted(NON_PRICE_ASSET_CLASSES))
async def test_no_non_price_class_is_normalised(tools: WorkerTools, asset_class: str) -> None:
    """MACRO was not the only one, and testing MACRO alone is what hid it.
    `^IRX` is class RISK_FREE and is a YIELD: percent-of-level on a 10bp move in
    the risk-free rate reports "+2.3%", the same category error the MACRO
    exclusion exists to prevent. GLOBAL_LIQUIDITY (an index), VOLATILITY and FX
    are the same kind. Parametrised over the constant so a class added there is
    covered without anyone remembering to extend this test."""
    ticker = f"T-{asset_class}"
    await _price(tools, ticker, level=4.3, speed=0.1, acceleration=0.02, asset_class=asset_class)
    row = (await tools.market_fetch([ticker], "1m"))[0]
    assert "speed_pct" not in row and "acceleration_pct" not in row
    assert row["speed"] == 0.1


async def test_macro_rows_are_not_normalised(tools: WorkerTools) -> None:
    """The load-bearing exclusion. T10Y2Y at 0.50 moving +0.14 is 14bp of
    steepening — already directly comparable to BAA10Y's +0.04. Dividing by its
    own level would report "+28%" for a curve move, which is not a quantity
    this codebase ever means."""
    await _price(tools, "T10Y2Y", level=0.5, speed=0.14, acceleration=0.05, asset_class="MACRO")
    row = (await tools.market_fetch(["T10Y2Y"], "1m"))[0]
    assert row["speed"] == 0.14
    assert "speed_pct" not in row and "acceleration_pct" not in row


async def test_float_noise_is_not_returned_to_the_model(tools: WorkerTools) -> None:
    """Same defect as `decision_cycle._num`, one boundary over: FRED published
    0.04 and binary subtraction makes it `0.039999999999999813`."""
    await _price(
        tools,
        "NOISY",
        level=1.64,
        speed=0.039999999999999813,
        acceleration=-0.05,
        asset_class="MACRO",
    )
    row = (await tools.market_fetch(["NOISY"], "1m"))[0]
    assert row["speed"] == 0.04


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


# -- the tables the Worker was never shown -----------------------------------


@pytest.mark.parametrize(
    "stmt",
    [
        "SELECT * FROM user_profile",
        "SELECT type, payload FROM event_log ORDER BY id DESC",
        "SELECT name FROM sqlite_master WHERE type = 'table'",
        "SELECT p.id FROM portfolio p JOIN user_profile u ON 1 = 1",
        "WITH caps AS (SELECT max_drawdown_pct FROM user_profile) SELECT * FROM caps",
        "SELECT id FROM portfolio UNION SELECT id FROM event_log",
    ],
)
def test_a_hidden_table_is_refused_however_it_is_reached(stmt: str) -> None:
    """HIDING A NAME IS NOT A BOUNDARY. `describe_schema` omitted these tables
    and `validate_sql` let every one of them through — including
    `sqlite_master`, which is the Worker's own documented first move when it has
    no schema. A subquery, a CTE, a JOIN and a UNION are all ways in, which is
    why the check is over the whole statement rather than its FROM clauses."""
    with pytest.raises(ToolInputError, match="not queryable"):
        validate_sql(stmt)


def test_the_schema_listing_and_the_gate_agree() -> None:
    """The two used to disagree, which is the whole defect: one set named what
    the listing hides, and nothing made the gate read it."""
    for hidden in SCHEMA_HIDDEN_TABLES:
        with pytest.raises(ToolInputError):
            validate_sql(f"SELECT * FROM {hidden}")


def test_an_ordinary_query_still_passes() -> None:
    """The refusal must not spread: these are the queries the Worker actually
    writes, and a gate that took them too would be worse than no gate."""
    assert "LIMIT" in validate_sql("SELECT id, sortino_rolling FROM portfolio")
    assert "LIMIT" in validate_sql(
        "SELECT i.id FROM invariant i JOIN backed_by b ON b.invariant_id = i.id"
    )
