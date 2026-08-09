"""The Worker's ONLY access to the database — three bridged tools (Task 5.1).

PRINCIPLE OF LEAST PRIVILEGE. The Worker is an investment expert with no
standing in the system's plumbing: it does not know the Planner exists, it
cannot write, and it cannot read anything these three functions do not hand
it (CLAUDE.md "Architecture in one screen"). Everything here is a boundary,
not a convenience layer — each limit below exists because the caller is a
language model whose output is not trusted input.

WHY A BLACKLIST *AND* A ROW CAP AND A SINGLE-STATEMENT RULE. None of the
three is sufficient alone:

- the keyword check stops the obvious write;
- the single-statement rule stops `SELECT 1; DROP TABLE invariant` — a
  blacklist scanning the whole string would catch that one, but not
  `SELECT 1; ATTACH DATABASE ...`, and enumerating every dangerous verb is
  a losing game;
- the row cap stops a `SELECT *` over `market_data` (200k rows) from
  blowing the context window, which is a denial-of-service on the Worker's
  own reasoning rather than on the database.

The connection is opened read-only where SQLite allows it, so the blacklist
is defence in depth rather than the only thing standing between a
hallucinated statement and the data.
"""

import functools
import logging
import re
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai.exceptions import ModelRetry

from investment.db.sqlite import InvestmentDB

logger = logging.getLogger(__name__)

# Reject the statement outright if any of these appears as a WORD (see
# `_contains_keyword` — substring matching would reject `SELECT created_at`
# for containing "CREATE").
SQL_KEYWORD_BLACKLIST = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "GRANT",
        # Not in the spec's list, added deliberately: ATTACH reaches OUTSIDE
        # the single file ADR-004 defines as the whole database, PRAGMA can
        # re-enable writes, and VACUUM/REINDEX rewrite storage. A read-only
        # tool has no use for any of them.
        "ATTACH",
        "DETACH",
        "PRAGMA",
        "VACUUM",
        "REINDEX",
        "REPLACE",
    }
)

PORTFOLIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")

# Internals the Worker has no business querying, and whose presence in a schema
# listing would only invite it. `event_log` is the append-only journal of what
# the system DID (it is unaware of Writeback — worker/agent.py); the embedding
# blobs are unreadable to it; `user_profile` reaches the owner's private caps,
# which bind through the gates and not through the Worker's reasoning.
SCHEMA_HIDDEN_TABLES = frozenset({"event_log", "user_profile", "sqlite_sequence"})


async def describe_schema(db: InvestmentDB) -> str:
    """The tables and columns the Worker may query, as prompt text.

    READ FROM THE DATABASE, never listed by hand: a hard-coded schema is wrong
    the first time a migration lands, and wrong SILENTLY — the Worker would go
    back to guessing, which is the exact failure this repairs.

    WHY THIS EXISTS. Measured on the M8b run of 2026-08-06: with no schema in
    its context the Worker opened every cycle by enumerating `sqlite_master`,
    and — because `db_query` caps results at 20 rows — split that enumeration in
    two to see all the tables. Three to five tool calls per cycle spent
    rediscovering the database, every month, before a single question about
    markets. It exhausted a budget of 8, then one of 12 (16 calls requested).

    The budget was never the problem. A tool that does not say what can be
    queried forces the model to find out, and the finding-out is charged to the
    same budget as the thinking. Raising the cap chases a moving target; naming
    the tables removes the need."""
    rows = await db.query(
        "SELECT m.name AS tbl, group_concat(i.name) AS cols "
        "FROM sqlite_master m JOIN pragma_table_info(m.name) i "
        "WHERE m.type = 'table' AND m.name NOT LIKE 'sqlite_%' "
        "GROUP BY m.name ORDER BY m.name"
    )
    lines = [
        f"  {row['tbl']}({row['cols']})"
        for row in rows
        if str(row["tbl"]) not in SCHEMA_HIDDEN_TABLES
    ]
    return "\n".join(lines)


# What a portfolio row exposes to the Worker. An allowlist, not a denylist:
# a column added to `portfolio` later must be opted IN, so the default for
# anything new is invisible rather than leaked.
PORTFOLIO_EXPOSED_FIELDS = (
    "id",
    "name",
    "defender",
    "enabled",
    "allocation",
    "benchmark",
    "max_drawdown_rule",
    "max_single_asset_pct",
    "sharpe_rolling",
    "sortino_rolling",
    "calmar_rolling",
    "max_drawdown",
    "volatility",
    "return_3m",
    "return_6m",
    "return_1y",
    "return_3y",
    "return_5y",
)

DB_QUERY_MAX_ROWS = 20
MARKET_FETCH_MAX_ROWS = 30

# `period` -> calendar days back from the latest row. Named periods rather
# than free-form dates: the Worker asks for a HORIZON, and a date range it
# composes itself is one more thing that can be wrong in a way the tool
# cannot detect.
MARKET_PERIODS: dict[str, int] = {
    "1m": 31,
    "3m": 92,
    "6m": 183,
    "1y": 366,
    "3y": 1096,
    "5y": 1827,
}

_WORD_RE = re.compile(r"[A-Za-z_]+")


class ToolInputError(ModelRetry, ValueError):
    """A tool refused its input. Carries a message meant for the MODEL: it is
    returned to the Worker so it can correct itself, so it must say what was
    wrong and what is allowed, never merely 'invalid'.

    `ModelRetry` is what makes that docstring TRUE. PydanticAI hands the model
    back only `ModelRetry`; every other exception escapes the agent loop and
    aborts the run. Until 2026-08-05 this was a plain `ValueError`, so a tool
    call the Worker got slightly wrong killed the whole UC8 cycle instead of
    being corrected — measured with `market_fetch(period='6mo')`, a Yahoo-style
    literal one character from the allowed `6m`. The failure was invisible under
    a model that happened never to guess wrong; it is a property of the code,
    not of the model.

    `ValueError` is kept in the bases so the refusal is still an ordinary
    programming error to any caller that uses these validators OUTSIDE an agent
    (`validate_sql` is deliberately testable without a DB or a model).

    This does not weaken the boundary: a retry re-enters `WorkerTools`, so the
    SQL blacklist, the ticker allow-list and the row caps all run again, and
    the retry itself is bounded by the agent's `tool_calls_limit` (worker/
    agent.py `WORKER_TOOL_CALLS_LIMIT`). A Worker that cannot get its own tool
    call right still fails the cycle — it just no longer fails on the first
    typo."""


def _contains_keyword(stmt: str) -> str | None:
    """The first blacklisted keyword present as a whole word, else None.

    Word-boundary matching, not substring: `SELECT created_at FROM invariant`
    contains "CREATE" as a substring and is perfectly legitimate. Rejecting
    it would teach the model to avoid ordinary column names for reasons it
    cannot see."""
    for word in _WORD_RE.findall(stmt.upper()):
        if word in SQL_KEYWORD_BLACKLIST:
            return str(word)
    return None


def _split_statements(stmt: str) -> list[str]:
    """Statements separated by `;`, ignoring a trailing one and empties.

    Deliberately naive — it does NOT parse strings or comments, so
    `SELECT ';'` counts as two. That errs toward REFUSING a legitimate query,
    which costs the Worker one retry; the opposite error costs a second
    statement executing unchecked."""
    return [part.strip() for part in stmt.split(";") if part.strip()]


def validate_sql(stmt: str) -> str:
    """The read-only gate for `db_query`, separated from the DB call so it is
    testable without a database. Returns the statement with a LIMIT enforced."""
    text = stmt.strip()
    if not text:
        raise ToolInputError("empty query")

    statements = _split_statements(text)
    if len(statements) > 1:
        raise ToolInputError(
            f"one statement per call, got {len(statements)}. "
            "Split them into separate db_query calls."
        )
    text = statements[0]

    keyword = _contains_keyword(text)
    if keyword is not None:
        raise ToolInputError(
            f"{keyword} is not allowed — db_query is READ-ONLY. "
            "Use SELECT ... only; the agent writes, you read."
        )
    if not text.upper().startswith(("SELECT", "WITH")):
        raise ToolInputError("query must start with SELECT or WITH")

    return _enforce_limit(text)


def _enforce_limit(text: str) -> str:
    """Append or tighten `LIMIT` so no call can return more than the cap.

    A model-supplied LIMIT is honoured when it is SMALLER, replaced when it is
    larger or absent. `_enforce_limit` never trusts the number it reads: the
    cap protects the Worker's own context window, so "the model asked for
    1000" is precisely the case that must not win."""
    match = re.search(r"\bLIMIT\s+(\d+)\s*$", text, flags=re.IGNORECASE)
    if match is None:
        return f"{text} LIMIT {DB_QUERY_MAX_ROWS}"
    if int(match.group(1)) <= DB_QUERY_MAX_ROWS:
        return text
    return f"{text[: match.start()].rstrip()} LIMIT {DB_QUERY_MAX_ROWS}"


def visible_refusal[**P, T](tool: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Log a refused tool call, then re-raise it unchanged.

    A REFUSAL COSTS A TURN AND LEFT NO TRACE, which made the tool budget
    impossible to reason about. `validate_sql` raises before `db_query` reaches
    its `logger.info`, so the log showed only the calls that SUCCEEDED while
    `tool_calls_limit` counted every attempt including the corrected ones.

    Measured 2026-08-09 at 2008-10-01 of the on-stack run: the limit reported
    15 tool calls, the log carried 7 — seven sensible, non-repeating queries and
    eight invisible refusals. A whole day was spent theorising about that
    budget (thoroughness? a loop? the new prompt?) from a log that could not
    show what was consuming it. The retries were doing their job; nothing said
    so.

    Kept as INFO rather than WARNING: a corrected tool call is the designed
    behaviour of `ToolInputError` being a `ModelRetry`, not a fault. It only
    has to be COUNTABLE."""

    @functools.wraps(tool)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await tool(*args, **kwargs)
        except ToolInputError as exc:
            logger.info("worker %s REFUSED (costs a turn): %s", tool.__name__, exc)
            raise

    return wrapper


class WorkerTools:
    """The three bridged tools, bound to one database connection.

    A class rather than free functions because PydanticAI registers BOUND
    methods as the agent's tools: the Worker calls `db_query(stmt)` and never
    sees the connection, which is what keeps it unaware of the storage layer
    it is not supposed to know exists."""

    def __init__(self, db: InvestmentDB) -> None:
        self._db = db

    @visible_refusal
    async def db_query(self, stmt: str) -> list[dict[str, Any]]:
        """Run a READ-ONLY SQL query. SELECT/WITH only, one statement, at most
        20 rows returned."""
        safe = validate_sql(stmt)
        logger.info("worker db_query: %s", safe)
        try:
            return await self._db.query(safe)
        except sqlite3.OperationalError as exc:
            # `validate_sql` checks SHAPE (read-only, one statement, LIMIT); it
            # cannot check MEANING, so a query naming a column that does not
            # exist reaches SQLite and comes back as OperationalError. That is a
            # fault in model-authored input, the same kind as a bad period
            # literal — and the message ("no such column: c") is precisely what
            # the model needs to fix it. Measured 2026-08-06: it aborted a whole
            # cycle instead.
            #
            # Deliberately NOT discriminated from the infrastructure
            # OperationalErrors ("database is locked", "disk I/O error"). A
            # message-matched exclusion list would be the fragile half of this
            # function, and it buys nothing: a genuinely unavailable database
            # fails the retry too, exhausts `WORKER_TOOL_CALLS_LIMIT` and aborts
            # the cycle with the SQLite text intact. The cost of guessing wrong
            # is a few wasted turns; the cost of the message-matching going
            # stale is a real fault silently reclassified.
            raise ToolInputError(
                f"the query failed: {exc}. Check names against the schema"
            ) from exc

    @visible_refusal
    async def market_fetch(self, tickers: list[str], period: str) -> list[dict[str, Any]]:
        """Recent market data for known tickers: (ts, ticker, level, speed,
        acceleration), most recent first, at most 30 rows in total."""
        if not tickers:
            raise ToolInputError("no tickers requested")
        if period not in MARKET_PERIODS:
            raise ToolInputError(
                f"unknown period {period!r} — use one of {', '.join(MARKET_PERIODS)}"
            )

        active = {
            str(row["ticker"])
            for row in await self._db.query("SELECT ticker FROM allowed_tickers WHERE active = 1")
        }
        unknown = [t for t in tickers if t not in active]
        if unknown:
            # Naming the offender, not just refusing: the Worker can only
            # correct a ticker it is told is wrong.
            raise ToolInputError(
                f"not in the allowed universe: {', '.join(sorted(unknown))}. "
                "Query allowed_tickers via db_query to see what exists."
            )

        placeholders = ", ".join(f":t{i}" for i in range(len(tickers)))
        params: dict[str, Any] = {f"t{i}": t for i, t in enumerate(tickers)}
        params["days"] = MARKET_PERIODS[period]
        params["cap"] = MARKET_FETCH_MAX_ROWS
        return await self._db.query(
            "SELECT ts, ticker, level, speed, acceleration FROM market_data "
            f"WHERE ticker IN ({placeholders}) "
            "  AND ts >= date((SELECT MAX(ts) FROM market_data), '-' || :days || ' days') "
            "ORDER BY ts DESC, ticker LIMIT :cap",
            **params,
        )

    @visible_refusal
    async def portfolio_check(self, portfolio_id: str) -> dict[str, Any]:
        """One portfolio's exposed fields. Returns `{}` if no such portfolio."""
        if not PORTFOLIO_ID_RE.match(portfolio_id):
            raise ToolInputError(
                f"malformed portfolio id {portfolio_id!r} — "
                "lowercase letters, digits and hyphens, max 50 characters"
            )
        columns = ", ".join(PORTFOLIO_EXPOSED_FIELDS)
        rows = await self._db.query(
            f"SELECT {columns} FROM portfolio WHERE id = :pid", pid=portfolio_id
        )
        return rows[0] if rows else {}
