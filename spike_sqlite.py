"""Task 0.5 — SQLite smoke test (ADR-004). Throwaway: superseded by schema.py at M1.

Validates, against a scratch DB (never the real investment.db):
  1. schema persists after reopen (WAL, synchronous=NORMAL, foreign_keys=ON)
  2. event_log-append + entity-insert rollback atomically as one transaction
  3. throughput: ~200k market_data rows batched-insert < 2 min; 756-row range read < 50ms
  4. 1000 embeddings (float32x384) -> brute-force cosine top-20 < 10ms
  5. asyncio harness: all calls via run_in_executor on ONE connection,
     10k mixed read/writes, no deadlock
"""

import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

DB_PATH = Path.home() / "data" / "investment" / "spike_investment.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS event_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL, event_date TEXT NOT NULL,
  type TEXT NOT NULL, source_uc TEXT NOT NULL,
  source_id TEXT, payload TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS invariant (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  status TEXT NOT NULL,
  weight_effective REAL,
  embedding BLOB);

CREATE TABLE IF NOT EXISTS market_data (
  ticker TEXT NOT NULL, asset_class TEXT NOT NULL, currency TEXT NOT NULL,
  ts TEXT NOT NULL, level REAL, speed REAL, acceleration REAL,
  PRIMARY KEY (ticker, ts));
"""


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def check_1_persistence() -> None:
    DB_PATH.unlink(missing_ok=True)
    conn = open_db()
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO invariant (id, title, description, status) VALUES (?,?,?,?)",
        ("inv-1", "test", "smoke test row", "proposed"),
    )
    conn.commit()
    conn.close()

    conn = open_db()
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"event_log", "invariant", "market_data"} <= tables, tables
    row = conn.execute("SELECT title FROM invariant WHERE id='inv-1'").fetchone()
    assert row == ("test",), row
    conn.close()
    print("[1/5] persistence after reopen: OK")


def check_2_atomic_rollback() -> None:
    conn = open_db()
    try:
        with conn:
            conn.execute(
                "INSERT INTO event_log (id, ts, event_date, type, source_uc, payload) "
                "VALUES (?,?,?,?,?,?)",
                ("evt-rollback", "2026-01-01T00:00:00Z", "2026-01-01", "test", "uc0", "{}"),
            )
            # Force a failure between the two writes (duplicate PK) -> rollback both.
            conn.execute(
                "INSERT INTO invariant (id, title, description, status) VALUES (?,?,?,?)",
                ("inv-1", "duplicate", "should fail", "proposed"),
            )
    except sqlite3.IntegrityError:
        pass

    row = conn.execute("SELECT 1 FROM event_log WHERE id='evt-rollback'").fetchone()
    assert row is None, "event_log append was not rolled back"
    conn.close()
    print("[2/5] atomic rollback (event_log + entity): OK")


def check_3_throughput() -> None:
    conn = open_db()
    n_rows = 200_000
    tickers = ["SPY", "TLT", "GLD", "TIP", "DBC"]
    start_date = datetime(1991, 1, 1, tzinfo=UTC)

    def rows() -> list[tuple[str, str, str, str, float, float, float]]:
        out = []
        per_ticker = n_rows // len(tickers)
        for ticker in tickers:
            for i in range(per_ticker):
                ts = (start_date + timedelta(days=i)).date().isoformat()
                out.append((ticker, "TRADABLE", "USD", ts, 100.0 + i * 0.01, 0.1, 0.01))
        return out

    data = rows()
    t0 = time.perf_counter()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO market_data "
            "(ticker, asset_class, currency, ts, level, speed, acceleration) "
            "VALUES (?,?,?,?,?,?,?)",
            data,
        )
    insert_s = time.perf_counter() - t0
    assert insert_s < 120, f"backfill took {insert_s:.1f}s, target < 120s"

    t0 = time.perf_counter()
    conn.execute(
        "SELECT ts, level FROM market_data WHERE ticker=? ORDER BY ts LIMIT 756",
        ("SPY",),
    ).fetchall()
    read_ms = (time.perf_counter() - t0) * 1000
    assert read_ms < 50, f"range read took {read_ms:.1f}ms, target < 50ms"
    conn.close()
    print(
        f"[3/5] throughput: {len(data)} rows inserted in {insert_s:.2f}s, "
        f"756-row range read in {read_ms:.2f}ms: OK"
    )


def check_4_embeddings() -> None:
    conn = open_db()
    rng = np.random.default_rng(42)
    vectors = rng.standard_normal((1000, 384)).astype(np.float32)

    with conn:
        for i, vec in enumerate(vectors):
            conn.execute(
                "INSERT OR REPLACE INTO invariant (id, title, description, status, embedding) "
                "VALUES (?,?,?,?,?)",
                (f"inv-emb-{i}", f"title {i}", "embedding smoke test", "proposed", vec.tobytes()),
            )

    rows = conn.execute(
        "SELECT id, embedding FROM invariant WHERE id LIKE 'inv-emb-%'"
    ).fetchall()
    matrix = np.stack(
        [np.frombuffer(blob, dtype=np.float32) for _, blob in rows]
    )
    query = vectors[0]

    t0 = time.perf_counter()
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
    cosine = (matrix @ query) / norms
    top20 = np.argsort(-cosine)[:20]
    cosine_ms = (time.perf_counter() - t0) * 1000
    assert cosine_ms < 10, f"cosine top-20 took {cosine_ms:.2f}ms, target < 10ms"
    assert top20[0] == 0, "query vector should match itself as top-1"
    conn.close()
    print(f"[4/5] embeddings: 1000 vectors, cosine top-20 in {cosine_ms:.3f}ms: OK")


async def check_5_asyncio_harness() -> None:
    # ADR-004 single-writer discipline: ONE connection, ONE worker thread —
    # every DB call is serialized through this executor, never the default
    # (multi-thread) pool, which would violate sqlite3's thread affinity.
    executor = ThreadPoolExecutor(max_workers=1)
    conn = executor.submit(open_db).result()
    loop = asyncio.get_running_loop()

    def write(i: int) -> None:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO market_data "
                "(ticker, asset_class, currency, ts, level, speed, acceleration) "
                "VALUES (?,?,?,?,?,?,?)",
                ("ASY", "TRADABLE", "USD", f"2026-01-{(i % 28) + 1:02d}", float(i), 0.0, 0.0),
            )

    def read(_i: int) -> None:
        conn.execute("SELECT COUNT(*) FROM market_data WHERE ticker='ASY'").fetchone()

    t0 = time.perf_counter()
    tasks = [
        loop.run_in_executor(executor, write if i % 2 == 0 else read, i)
        for i in range(10_000)
    ]
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - t0
    await loop.run_in_executor(executor, conn.close)
    executor.shutdown()
    print(f"[5/5] asyncio harness: 10000 mixed read/writes in {elapsed:.2f}s, no deadlock: OK")


def main() -> None:
    check_1_persistence()
    check_2_atomic_rollback()
    check_3_throughput()
    check_4_embeddings()
    asyncio.run(check_5_asyncio_harness())
    DB_PATH.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
    print("\nAll 5 SQLite smoke checks passed (ADR-004 GO).")


if __name__ == "__main__":
    main()
