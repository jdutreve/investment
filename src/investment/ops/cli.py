"""`invest` CLI (docs/TASKS.md Task 6ter.2) — M1 minimal subset: `sql` and
`status` only. The rest of the command set (ranking, proposals,
accept/reject, chat, ...) arrives once ops/api.py and the agent process
exist (M9-M10); this is the "falls back to direct read-only SQLite when
the agent is down" path, used unconditionally for now since there is no
agent process yet.

Reads are direct on SQLite, read-only (docs/CLAUDE.md "User interfaces —
one command layer": writes only ever go through the running agent).
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from investment.config import Settings

_STATUS_ENTITY_COUNTS = (
    "framework", "regime_type", "invariant", "strategy", "scenario", "portfolio",
)


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def cmd_sql(db_path: Path, query: str) -> None:
    con = _connect_readonly(db_path)
    try:
        rows = con.execute(query).fetchall()
    except sqlite3.Error as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    finally:
        con.close()

    if not rows:
        print("(no rows)")
        return
    cols = rows[0].keys()
    print(" | ".join(cols))
    for row in rows:
        print(" | ".join(str(row[c]) for c in cols))


def cmd_status(db_path: Path) -> None:
    con = _connect_readonly(db_path)
    try:
        defender = con.execute(
            "SELECT id, name FROM portfolio WHERE defender = 1"
        ).fetchone()
        last_chain = con.execute(
            "SELECT last_chain_success FROM detector_state WHERE id = 'singleton'"
        ).fetchone()
        pending = con.execute(
            "SELECT COUNT(*) AS n FROM proposal WHERE user_response = 'pending'"
        ).fetchone()
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in _STATUS_ENTITY_COUNTS
        }
    finally:
        con.close()

    print(f"defender: {defender['name']} ({defender['id']})" if defender else "defender: (none)")
    print(f"last chain success: {last_chain['last_chain_success'] if last_chain else '(never)'}")
    print(f"pending proposals: {pending['n']}")
    print(f"seed counts: {counts}")


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    parser = argparse.ArgumentParser(prog="invest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sql_parser = subparsers.add_parser("sql", help="read-only SQL query")
    sql_parser.add_argument("query")

    subparsers.add_parser(
        "status", help="regime, defender, last chain, pending items"
    )

    args = parser.parse_args()
    if args.command == "sql":
        cmd_sql(settings.db_path, args.query)
    elif args.command == "status":
        cmd_status(settings.db_path)


if __name__ == "__main__":
    main()
