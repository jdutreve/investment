"""SQLite wrapper (ADR-004) — see docs/TASKS.md Task 1.2.

Agent is the sole writer, ONE connection, every call serialized through a
single-worker executor (matching the ADR-004 discipline verified in
spike_sqlite.py check #5 — the default multi-thread executor violates
sqlite3's thread affinity; a single dedicated worker thread does not).

Transactions: the connection runs in true autocommit mode
(`isolation_level=None`) so a lone write call commits itself immediately,
while `transaction()` issues an explicit BEGIN/COMMIT/ROLLBACK to group
several calls atomically on the same connection — this is what lets
`append_event()` (EventLog) and the related vertex/edge commit land in one
all-or-nothing unit, per CLAUDE.md "EventLog — source of truth for UC8".
"""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TypeVar

from ulid import ULID

from investment.db.schema import (
    DOCUMENT_TABLES,
    ENTITY_TABLES,
    RELATION_TABLES,
    SCHEMA_SQL,
    TRACE_EXEMPT,
    TS_TABLES,
)

_T = TypeVar("_T")

# relation table -> (from-column, to-column), per DATA_MODELS.md "M:N —
# association tables". Every M:N relation has exactly two FK columns.
EDGE_COLUMNS: dict[str, tuple[str, str]] = {
    "favors": ("regime_type_id", "strategy_id"),
    "backed_by": ("strategy_id", "invariant_id"),
    "holds": ("portfolio_id", "strategy_id"),
    "designed_for": ("portfolio_id", "regime_type_id"),
    "supports": ("passage_id", "invariant_id"),
}

_VALID_TABLES = ENTITY_TABLES | RELATION_TABLES | TS_TABLES | DOCUMENT_TABLES


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _jsonify(props: dict[str, Any]) -> dict[str, Any]:
    """MAP/STRING[] columns are stored as JSON1 TEXT (DATA_MODELS.md 'Physical
    mapping'): any dict/list value is serialized on write, uniformly,
    regardless of destination column."""
    return {
        k: (json.dumps(v) if isinstance(v, dict | list) else v)
        for k, v in props.items()
    }


class InvestmentDB:
    """SQLite wrapper — agent sole writer, ONE connection, all calls
    serialized through asyncio run_in_executor. Explicit transactions."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._executor = ThreadPoolExecutor(max_workers=1)
        # check_same_thread=False: the connection is created here (whatever
        # thread calls __init__) but every subsequent use is routed through
        # the single worker thread above — sqlite3's own affinity guard
        # would otherwise reject that (see spike_sqlite.py check #5).
        self._con = sqlite3.connect(
            self._db_path, check_same_thread=False, isolation_level=None
        )
        self._con.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON"):
            self._con.execute(f"PRAGMA {pragma}")
        self._con.executescript(SCHEMA_SQL)
        self._columns_cache: dict[str, set[str]] = {}

    async def _call(self, fn: Callable[[], _T]) -> _T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    def _table_columns(self, table: str) -> set[str]:
        if table not in self._columns_cache:
            rows = self._con.execute(f"PRAGMA table_info({table})").fetchall()
            self._columns_cache[table] = {row["name"] for row in rows}
        return self._columns_cache[table]

    def _require_valid_table(self, table: str) -> None:
        if table not in _VALID_TABLES:
            raise ValueError(f"unknown table: {table!r}")

    # -- read ----------------------------------------------------------

    async def query(self, stmt: str, **params: Any) -> list[dict[str, Any]]:
        """Read-only query; named parameters via `:name` placeholders."""

        def _run() -> list[dict[str, Any]]:
            cur = self._con.execute(stmt, params)
            return [dict(row) for row in cur.fetchall()]

        return await self._call(_run)

    async def query_ts(self, type: str, where: str, limit: int) -> list[dict[str, Any]]:
        """Trusted-caller-only: `where` is interpolated raw (no LLM-facing
        caller uses this — the Worker's bridged `market_fetch` tool has its
        own separate whitelist, per CLAUDE.md 'Bridged functions')."""
        self._require_valid_table(type)

        def _run() -> list[dict[str, Any]]:
            cur = self._con.execute(f"SELECT * FROM {type} WHERE {where} LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]

        return await self._call(_run)

    # -- write -----------------------------------------------------------

    async def command(self, stmt: str, **params: Any) -> None:
        """Single write statement; commits immediately unless called inside
        an open `transaction()` block."""

        def _run() -> None:
            self._con.execute(stmt, params)

        await self._call(_run)

    async def create_vertex(self, type: str, props: dict[str, Any]) -> str:
        """INSERT a new vertex row; fails on a duplicate id (use
        `upsert_vertex` for idempotent writes, e.g. UC0 seed)."""
        if type not in TRACE_EXEMPT and not props.get("trace"):
            raise ValueError(f"trace mandatory for {type}")
        self._require_valid_table(type)

        def _run() -> str:
            row = self._stamp_and_jsonify(type, props)
            vertex_id: str = row.setdefault("id", str(ULID()))
            cols = list(row.keys())
            placeholders = ", ".join(f":{c}" for c in cols)
            stmt = f"INSERT INTO {type} ({', '.join(cols)}) VALUES ({placeholders})"
            self._con.execute(stmt, row)
            return vertex_id

        return await self._call(_run)

    async def upsert_vertex(self, type: str, id: str, props: dict[str, Any]) -> str:
        """Idempotent by id — UC0 seed re-runs safely. Uses ON CONFLICT DO
        UPDATE rather than INSERT OR REPLACE (which deletes-then-reinserts
        the row) so `created_at` — set once on the first insert — survives
        every later re-upsert instead of being bumped to "now" each time."""
        if type not in TRACE_EXEMPT and not props.get("trace"):
            raise ValueError(f"trace mandatory for {type}")
        self._require_valid_table(type)

        def _run() -> str:
            row = self._stamp_and_jsonify(type, props)
            row["id"] = id
            cols = list(row.keys())
            placeholders = ", ".join(f":{c}" for c in cols)
            update_cols = [c for c in cols if c not in ("id", "created_at")]
            conflict_action = (
                f"DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in update_cols)}"
                if update_cols
                else "DO NOTHING"
            )
            stmt = (
                f"INSERT INTO {type} ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) {conflict_action}"
            )
            self._con.execute(stmt, row)
            return id

        return await self._call(_run)

    def _stamp_and_jsonify(self, table: str, props: dict[str, Any]) -> dict[str, Any]:
        row = _jsonify(props)
        columns = self._table_columns(table)
        now = _utc_now_iso()
        if "created_at" in columns and "created_at" not in row:
            row["created_at"] = now
        if "updated_at" in columns and "updated_at" not in row:
            row["updated_at"] = now
        return row

    async def create_edge(
        self, type: str, from_id: str, to_id: str, props: dict[str, Any] | None = None
    ) -> None:
        """Idempotent (INSERT OR REPLACE) — every M:N relation's composite
        PK (from, to) makes this naturally safe to re-run."""
        if type not in EDGE_COLUMNS:
            raise ValueError(f"unknown edge table: {type!r}")
        from_col, to_col = EDGE_COLUMNS[type]

        def _run() -> None:
            row = _jsonify(props or {})
            row[from_col] = from_id
            row[to_col] = to_id
            cols = list(row.keys())
            placeholders = ", ".join(f":{c}" for c in cols)
            stmt = f"INSERT OR REPLACE INTO {type} ({', '.join(cols)}) VALUES ({placeholders})"
            self._con.execute(stmt, row)

        await self._call(_run)

    async def append_event(
        self,
        type: str,
        source_uc: str,
        source_id: str | None,
        payload: dict[str, Any],
        event_date: date | None = None,
    ) -> str:
        """EventLog append — MUST be called before the related vertex/edge
        commit, in the same `transaction()` block. id = monotonic ULID (the
        canonical append order); event_date = domain date, defaults to
        today. See docs/DATA_MODELS.md 'Ordering semantics'."""

        def _run() -> str:
            event_id = str(ULID())
            self._con.execute(
                "INSERT INTO event_log (id, ts, event_date, type, source_uc, source_id, payload) "
                "VALUES (:id, :ts, :event_date, :type, :source_uc, :source_id, :payload)",
                {
                    "id": event_id,
                    "ts": _utc_now_iso(),
                    "event_date": (event_date or date.today()).isoformat(),
                    "type": type,
                    "source_uc": source_uc,
                    "source_id": source_id,
                    "payload": json.dumps(payload),
                },
            )
            return event_id

        return await self._call(_run)

    async def append_ts(
        self, type: str, ts: datetime, tags: dict[str, Any], fields: dict[str, Any]
    ) -> None:
        """Idempotent (INSERT OR REPLACE) append to a time-series table —
        catch-up re-runs overwrite same-day rows rather than duplicate."""
        if type not in TS_TABLES:
            raise ValueError(f"not a time-series table: {type!r}")

        def _run() -> None:
            row = {**tags, "ts": ts.date().isoformat(), **fields}
            cols = list(row.keys())
            placeholders = ", ".join(f":{c}" for c in cols)
            stmt = f"INSERT OR REPLACE INTO {type} ({', '.join(cols)}) VALUES ({placeholders})"
            self._con.execute(stmt, row)

        await self._call(_run)

    # -- transactions ------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["InvestmentDB"]:
        """Groups several write calls into one atomic unit on the same
        connection (BEGIN ... COMMIT/ROLLBACK) — required whenever an
        EventLog append must land together with its vertex/edge commit."""
        await self._call(lambda: self._con.execute("BEGIN"))
        try:
            yield self
        except Exception:
            await self._call(self._con.rollback)
            raise
        else:
            await self._call(self._con.commit)

    # -- lifecycle -----------------------------------------------------

    async def close(self) -> None:
        """Checkpoints the WAL before closing (CLAUDE.md 'Graceful
        shutdown') — never drop the connection mid-write."""

        def _run() -> None:
            self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._con.close()

        await self._call(_run)
        self._executor.shutdown()
