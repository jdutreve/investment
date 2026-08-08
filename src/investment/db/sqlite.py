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
all-or-nothing unit, per the CLAUDE.md "EventLog" rule.

Serializing single calls is NOT enough to make that unit atomic, and the
difference is the reason for `_tx_lock` below. The executor serializes each
`_call`; `transaction()` spans many of them with `await`s in between, so on a
single connection any other coroutine that writes during that window writes
INSIDE the open BEGIN — it commits when the transaction commits, and is rolled
back when the transaction rolls back. A second concurrent `transaction()` is
worse: its BEGIN raises "cannot start a transaction within a transaction", and
its COMMIT ends the FIRST one's unit early. So every call is serialized at
TRANSACTION granularity, not statement granularity.

Reads are serialized too, and for a reason distinct from atomicity: with ONE
connection a concurrent reader sits INSIDE the open transaction, so it reads
uncommitted rows — and if that transaction rolls back it decided on state that
never existed. `_serialized` says why at length.
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
    ADDED_COLUMNS,
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
    return {k: (json.dumps(v) if isinstance(v, dict | list) else v) for k, v in props.items()}


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
        self._con = sqlite3.connect(self._db_path, check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON"):
            self._con.execute(f"PRAGMA {pragma}")
        self._con.executescript(SCHEMA_SQL)
        for table, column, decl in ADDED_COLUMNS:
            existing = {r["name"] for r in self._con.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self._con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self._columns_cache: dict[str, set[str]] = {}
        # Transaction-granularity serialization (see module docstring). The
        # owner task is tracked alongside the lock because the transaction's OWN
        # writes must pass straight through — they are what the BEGIN is for —
        # while every other task's must wait for the COMMIT.
        self._tx_lock = asyncio.Lock()
        self._tx_owner: asyncio.Task[Any] | None = None
        # Monotonic floor for event ids, re-seeded from the DB so the
        # canonical append order survives restarts (and clock steps between
        # them) — see _next_event_id().
        row = self._con.execute("SELECT MAX(id) FROM event_log").fetchone()
        self._last_event_id: str = row[0] or ""

    async def _call(self, fn: Callable[[], _T]) -> _T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    async def _serialized(self, fn: Callable[[], _T]) -> _T:
        """Every read AND write goes through here: it runs immediately when no
        transaction is open or when this task owns the open one, and otherwise
        waits for that transaction to finish rather than observing it midway.

        READS TAKE THIS PATH TOO, and an earlier version of this guard exempted
        them on the reasoning that a read inside another task's transaction
        "sees the same thing it would see one statement earlier". That is wrong
        in the case that matters. There is ONE connection (ADR-004), so a reader
        is inside the writer's transaction, not outside it: it sees uncommitted
        rows, and if that transaction ROLLS BACK it has read state that never
        existed and will never exist. Every write in this codebase is
        EventLog-first inside a transaction, so the rows most exposed are
        exactly the ones decisions are made from. Serializing readers behind
        writers costs a wait; not serializing them costs a decision taken on a
        phantom.

        The check and the executor submission are not separated by an `await`,
        so no other coroutine can open a transaction between them — the guard
        cannot be raced from the event loop, and the single-worker executor
        preserves the submission order from there."""
        owner = self._tx_owner
        if owner is not None and owner is not asyncio.current_task():
            async with self._tx_lock:
                return await self._call(fn)
        return await self._call(fn)

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

        return await self._serialized(_run)

    async def query_ts(self, type: str, where: str, limit: int) -> list[dict[str, Any]]:
        """Trusted-caller-only: `where` is interpolated raw (no LLM-facing
        caller uses this — the Worker's bridged `market_fetch` tool has its
        own separate whitelist, per the Worker bridged-tools rule in
        CLAUDE.md 'Architecture in one screen')."""
        self._require_valid_table(type)

        def _run() -> list[dict[str, Any]]:
            cur = self._con.execute(f"SELECT * FROM {type} WHERE {where} LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]

        return await self._serialized(_run)

    # -- write -----------------------------------------------------------

    async def command(self, stmt: str, **params: Any) -> None:
        """Single write statement; commits immediately unless called inside
        an open `transaction()` block."""

        def _run() -> None:
            self._con.execute(stmt, params)

        await self._serialized(_run)

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

        return await self._serialized(_run)

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

        return await self._serialized(_run)

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

        await self._serialized(_run)

    def _next_event_id(self) -> str:
        """Strictly-increasing ULID for event_log.id — THE canonical append
        order (DATA_MODELS.md 'Ordering semantics'), so monotonicity is a
        hard guarantee, enforced here rather than trusted to the library:
        python-ulid samples the clock twice (timestamp in __init__,
        monotonicity decision in the provider), so a ULID minted as the
        millisecond ticks over can sort BELOW its predecessor. If that
        happens, take last_id + 1 (128-bit increment) instead. Only ever
        called on the single executor thread — no locking needed."""
        candidate = str(ULID())
        if candidate <= self._last_event_id:
            bumped = int.from_bytes(ULID.from_str(self._last_event_id).bytes, "big") + 1
            candidate = str(ULID.from_bytes(bumped.to_bytes(16, "big")))
        self._last_event_id = candidate
        return candidate

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
            event_id = self._next_event_id()
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

        return await self._serialized(_run)

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

        await self._serialized(_run)

    async def append_ts_batch(self, type: str, rows: list[dict[str, Any]]) -> None:
        """Batched idempotent append (INSERT OR REPLACE, executemany, one
        transaction) — the throughput path validated in spike_sqlite.py
        check #3 (200k rows < 2 min); `append_ts` alone would pay one
        executor round-trip per row, far too slow for a 35y backfill across
        ~20 series (docs/TASKS.md Task 2.1). Each row must already contain
        `ts` (date.isoformat()) plus every tag/field column."""
        if type not in TS_TABLES:
            raise ValueError(f"not a time-series table: {type!r}")
        if not rows:
            return

        def _run() -> None:
            cols = list(rows[0].keys())
            placeholders = ", ".join(f":{c}" for c in cols)
            stmt = f"INSERT OR REPLACE INTO {type} ({', '.join(cols)}) VALUES ({placeholders})"
            # Explicit BEGIN/COMMIT (isolation_level=None means the connection
            # is otherwise in true autocommit — see class docstring): without
            # it, executemany would fsync once per row instead of once for
            # the whole batch, defeating the throughput this method exists for.
            self._con.execute("BEGIN")
            try:
                self._con.executemany(stmt, rows)
            except Exception:
                self._con.rollback()
                raise
            else:
                self._con.commit()

        await self._serialized(_run)

    # -- transactions ------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["InvestmentDB"]:
        """Groups several write calls into one atomic unit on the same
        connection (BEGIN ... COMMIT/ROLLBACK) — required whenever an
        EventLog append must land together with its vertex/edge commit.

        Holds `_tx_lock` for the WHOLE unit, not per statement: the atomicity
        this exists for is only real if no other task can write into the open
        BEGIN or issue its own (see module docstring). Concurrency is real here
        — the inbox watcher's ingestion batch and the Monday chain are separate
        tasks on one connection, and UC9 can trigger an ad-hoc UC8 re-run
        alongside either.

        NESTING is refused rather than silently joined, and the guard is load
        bearing: `asyncio.Lock` is not reentrant, so a same-task re-entry would
        WAIT on a lock its own task already holds and hang forever. This turns a
        silent deadlock into an immediate error naming the nested `async with`.
        No caller nests today — composite paths pass the `tx` handle down —
        which is the convention the message points back at.

        `rollback` on a failed BEGIN is a no-op on this autocommit connection
        (`isolation_level=None`), so the error path needs no second guard."""
        if self._tx_owner is asyncio.current_task():
            raise RuntimeError("nested transaction() — pass the open handle down instead")
        async with self._tx_lock:
            self._tx_owner = asyncio.current_task()
            try:
                await self._call(lambda: self._con.execute("BEGIN"))
                yield self
            except Exception:
                await self._call(self._con.rollback)
                raise
            else:
                await self._call(self._con.commit)
            finally:
                self._tx_owner = None

    # -- lifecycle -----------------------------------------------------

    async def close(self) -> None:
        """Checkpoints the WAL before closing (CLAUDE.md 'Dev standards'
        shutdown rule) — never drop the connection mid-write."""

        def _run() -> None:
            self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._con.close()

        await self._call(_run)
        self._executor.shutdown()
