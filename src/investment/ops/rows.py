"""Reading SQLite from a FRONT — the small shared half of ADR-005's read side.

`unnest_json_columns` WAS PRIVATE TO `ops/cli.py` UNTIL THE SECOND FRONT
ARRIVED, which is the whole reason this module exists — the project's most
productive review question applied to itself (CLAUDE.md: "WHEN A SECOND ONE
ARRIVES, FIND WHAT NAMED THE FIRST"). It named the CLI because the CLI was the
only front turning rows into JSON; `ops/api.py` does the same thing for the
browser, and a copy in each would be two answers to "is this column JSON?" —
the kind that agree until one of them is fixed.

`connect_readonly` has ONE caller, and that is not an oversight. The CLI opens
the database file itself precisely because it must work when the agent is down
(docs/TASKS.md Phase 6ter, offline matrix). The API cannot want this: it runs
INSIDE the agent, so it reads through the agent's single connection — a second
handle would sit on a different committed snapshot and split one page across two
instants (`ops/api.py` module docstring). It lives here rather than in `cli.py`
because the two functions are one subject, and a reader asking "how does a front
read this database" should find both answers in one place, including the reason
they differ.
"""

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """A read-only connection to the live database.

    `mode=ro` is enforced by SQLite itself, not by discipline: the handle
    cannot write even if a caller passes it a mutating statement, which is what
    keeps ADR-004's single-writer rule true no matter what a front does. A
    read-only connection also never creates the file, so pointing a front at a
    wrong path FAILS instead of silently producing an empty database that looks
    like an agent which has never run."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def unnest_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    """Columns stored as JSON1 TEXT (MAP/STRING[], DATA_MODELS.md 'Physical
    mapping') come back from SQLite as JSON-encoded strings; parse them back
    into native lists/dicts so a JSON consumer nests them instead of
    double-encoding them (`"tags": "[\\"a\\"]"` -> `"tags": ["a"]`)."""
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, str) and value[:1] in "[{":
            with contextlib.suppress(ValueError):
                value = json.loads(value)
        result[key] = value
    return result
