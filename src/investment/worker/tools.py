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

import logging
import re
from typing import Any

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


class ToolInputError(ValueError):
    """A tool refused its input. Carries a message meant for the MODEL: it is
    returned to the Worker so it can correct itself, so it must say what was
    wrong and what is allowed, never merely 'invalid'."""


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


class WorkerTools:
    """The three bridged tools, bound to one database connection.

    A class rather than free functions because PydanticAI registers BOUND
    methods as the agent's tools: the Worker calls `db_query(stmt)` and never
    sees the connection, which is what keeps it unaware of the storage layer
    it is not supposed to know exists."""

    def __init__(self, db: InvestmentDB) -> None:
        self._db = db

    async def db_query(self, stmt: str) -> list[dict[str, Any]]:
        """Run a READ-ONLY SQL query. SELECT/WITH only, one statement, at most
        20 rows returned."""
        safe = validate_sql(stmt)
        logger.info("worker db_query: %s", safe)
        return await self._db.query(safe)

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
