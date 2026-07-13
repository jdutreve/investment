"""`invest` CLI (docs/TASKS.md Task 6ter.2) — partial: `sql`, `status`,
`invariants`. The rest of the command set (ranking, proposals,
accept/reject, chat, ...) arrives once ops/api.py and the agent process
exist (M9-M10); this is the "falls back to direct read-only SQLite when
the agent is down" path, used unconditionally for now since there is no
agent process yet.

Reads are direct on SQLite, read-only (ADR-005 one-command-layer rule:
writes only ever go through the running agent — see docs/DECISIONS.md).
"""

import argparse
import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from investment.config import Settings

_STATUS_ENTITY_COUNTS = (
    "framework", "regime_type", "invariant", "strategy", "scenario", "portfolio",
)


class _Ansi:
    """Plain ANSI escapes — no new dependency for a handful of colors
    (CLAUDE.md Stack: nothing beyond the listed frameworks). Never applied to
    --json output: that has to stay valid, unescaped JSON for scripting
    (`| jq`, `| python -c ...`)."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    YELLOW = "\033[33m"


def _color_enabled() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _c(text: str, *codes: str) -> str:
    if not _color_enabled():
        return text
    return f"{''.join(codes)}{text}{_Ansi.RESET}"


def _label(text: str) -> str:
    return _c(text, _Ansi.DIM)


def _value(text: str) -> str:
    return _c(text, _Ansi.BOLD, _Ansi.CYAN)


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _unnest_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    """Columns stored as JSON1 TEXT (MAP/STRING[], DATA_MODELS.md 'Physical
    mapping') come back from SQLite as JSON-encoded strings; parse them back
    into native lists/dicts so --json nests them instead of double-encoding
    (`"tags": "[\\"a\\"]"` -> `"tags": ["a"]`)."""
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, str) and value[:1] in "[{":
            with contextlib.suppress(ValueError):
                value = json.loads(value)
        result[key] = value
    return result


def cmd_sql(db_path: Path, query: str, as_json: bool) -> None:
    con = _connect_readonly(db_path)
    try:
        rows = con.execute(query).fetchall()
    except sqlite3.Error as e:
        print(_c(f"error: {e}", _Ansi.RED), file=sys.stderr)
        raise SystemExit(1) from e
    finally:
        con.close()

    if as_json:
        print(json.dumps(
            [_unnest_json_columns(dict(row)) for row in rows], indent=2, ensure_ascii=False
        ))
        return

    if not rows:
        print(_c("(no rows)", _Ansi.DIM))
        return
    cols = rows[0].keys()
    print(_c(" | ".join(cols), _Ansi.BOLD, _Ansi.CYAN))
    for row in rows:
        print(" | ".join(str(row[c]) for c in cols))


def _resolve_regime_type_id(con: sqlite3.Connection, regime: str) -> str:
    """--regime accepts either the canonical RegimeType id or one of its
    aliases (e.g. 'stagflation' -> 'falling-growth-rising-inflation') —
    tags always store the canonical id."""
    row = con.execute(
        "SELECT id FROM regime_type "
        "WHERE id = :regime "
        "   OR EXISTS (SELECT 1 FROM json_each(aliases) WHERE value = :regime)",
        {"regime": regime},
    ).fetchone()
    return str(row["id"]) if row else regime


def cmd_invariants(
    db_path: Path,
    regime: str | None,
    tag: str | None,
    status: str | None,
    top: int | None,
    as_json: bool,
) -> None:
    """docs/TASKS.md Task 6ter.2 `invest invariants` — ranked by
    weight_effective DESC, the same conviction ordering the Worker reads."""
    where = []
    params: dict[str, str] = {}
    con = _connect_readonly(db_path)
    if regime is not None:
        where.append(
            "EXISTS (SELECT 1 FROM json_each(invariant.tags) WHERE value = :regime_tag)"
        )
        params["regime_tag"] = f"regime:{_resolve_regime_type_id(con, regime)}"
    if tag is not None:
        where.append("EXISTS (SELECT 1 FROM json_each(invariant.tags) WHERE value = :tag)")
        params["tag"] = tag
    if status is not None:
        where.append("status = :status")
        params["status"] = status

    query = (
        "SELECT id, title, author, status, weight_effective, tags FROM invariant"
        + (f" WHERE {' AND '.join(where)}" if where else "")
        + " ORDER BY weight_effective DESC"
        + (" LIMIT :top" if top is not None else "")
    )
    if top is not None:
        params["top"] = str(top)

    try:
        rows = con.execute(query, params).fetchall()
    finally:
        con.close()

    if as_json:
        print(json.dumps(
            [_unnest_json_columns(dict(row)) for row in rows], indent=2, ensure_ascii=False
        ))
        return

    if not rows:
        print(_c("(no invariants match)", _Ansi.DIM))
        return
    header = f"{'id':38s} {'author':7s} {'status':10s} {'weight':7s} tags"
    print(_c(header, _Ansi.BOLD, _Ansi.CYAN))
    for row in rows:
        tags = ", ".join(json.loads(row["tags"])) if row["tags"] else ""
        weight = f"{row['weight_effective']:.2f}" if row["weight_effective"] is not None else "-"
        author = row["author"] or "-"
        print(f"{row['id']:38s} {author:7s} {row['status']:10s} {weight:7s} {tags}")


def cmd_status(db_path: Path, as_json: bool) -> None:
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

    if as_json:
        print(json.dumps({
            "defender": dict(defender) if defender else None,
            "last_chain_success": last_chain["last_chain_success"] if last_chain else None,
            "pending_proposals": pending["n"],
            "seed_counts": counts,
        }, indent=2, ensure_ascii=False))
        return

    defender_text = f"{defender['name']} ({defender['id']})" if defender else "(none)"
    chain_text = last_chain["last_chain_success"] if last_chain else "(never)"
    pending_color = _Ansi.YELLOW if pending["n"] else _Ansi.CYAN

    print(f"{_label('defender:')} {_value(defender_text)}")
    print(f"{_label('last chain success:')} {_value(chain_text)}")
    print(f"{_label('pending proposals:')} {_c(str(pending['n']), _Ansi.BOLD, pending_color)}")
    print(f"{_label('seed counts:')} {_value(str(counts))}")


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    parser = argparse.ArgumentParser(prog="invest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sql_parser = subparsers.add_parser("sql", help="read-only SQL query")
    sql_parser.add_argument("query")
    sql_parser.add_argument("--json", action="store_true", help="output as JSON")

    status_parser = subparsers.add_parser(
        "status", help="regime, defender, last chain, pending items"
    )
    status_parser.add_argument("--json", action="store_true", help="output as JSON")

    invariants_parser = subparsers.add_parser(
        "invariants", help="list invariants, ranked by weight_effective"
    )
    invariants_parser.add_argument("--regime", help="filter by regime tag, e.g. stagflation")
    invariants_parser.add_argument("--tag", help="filter by an exact tag, e.g. asset:GLD")
    invariants_parser.add_argument("--status", help="'proposed' | 'integrated' | 'rejected'")
    invariants_parser.add_argument("--top", type=int, help="limit to the top N")
    invariants_parser.add_argument("--json", action="store_true", help="output as JSON")

    args = parser.parse_args()
    if args.command == "sql":
        cmd_sql(settings.db_path, args.query, args.json)
    elif args.command == "status":
        cmd_status(settings.db_path, args.json)
    elif args.command == "invariants":
        cmd_invariants(
            settings.db_path, args.regime, args.tag, args.status, args.top, args.json
        )


if __name__ == "__main__":
    main()
