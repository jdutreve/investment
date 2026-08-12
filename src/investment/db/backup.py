"""Online backup of the live database (docs/TASKS.md Phase 7: "`sqlite3
.backup` (online, WAL-safe) → `~/data/investment/backups/investment-<date>.db`
after every successful weekly chain and every ingestion batch; keep 14 files").

NO CLOCK-BASED BACKUP, and the spec is explicit about why: the data changes
through exactly two paths — the weekly chain and an ingestion batch — plus UC9
decisions, which ride the next one. A nightly timer would fire on a sleeping
laptop (ADR-002) and, on the nights it did run, copy a database nothing had
touched.

`sqlite3`'s BACKUP API and never a file copy: the live database is WAL, the agent
may be mid-write, and only the backup API produces a transactionally consistent
copy of it. Same reasoning as `db/as_of_snapshot.py`, which does this on the
other side of the system — a plain `cp` of a WAL database can capture a torn
state that restores as a corrupt file, and it would do so silently.

ITS OWN CONNECTION, deliberately, and not the agent's. ADR-004 gives the agent
ONE connection through which every write is serialized; borrowing it for a
multi-second copy would block every writer behind it. The backup source is
opened read-only (`mode=ro`), so this path cannot write to the live file even by
accident.
"""

import logging
import sqlite3
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

# How many backups survive a prune. 14 = two weeks of weekly runs plus the ingestion
# batches in between, which is the horizon over which "something went wrong last
# week" is still actionable — beyond it, a stale copy of a derived database is
# worth less than the disk it sits on (every artefact in here is recomputable
# from market data and the seed).
KEEP_BACKUPS = 14

# One file per DAY, not per run: an ingestion batch can fire several times an
# hour, and a per-run name would push a whole day of chain history out of a
# 14-file window in an afternoon. The last write of a day wins, which is the one
# a restore would want anyway.
_STEM = "investment-"


def backup_path(backups_dir: Path, day: date) -> Path:
    return backups_dir / f"{_STEM}{day.isoformat()}.db"


def backup_database(db_path: Path, backups_dir: Path, *, today: date | None = None) -> Path:
    """Copy the live database to `backups_dir`, prune to `KEEP_BACKUPS`, return
    the file written.

    Overwrites the day's file if it exists — see `_STEM`. The copy is made
    first and the prune second, so a prune that fails cannot leave the caller
    without today's backup."""
    today = today or date.today()
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_path(backups_dir, today)

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    pruned = prune_backups(backups_dir)
    logger.info("backup written: %s (%d pruned)", dest, pruned)
    return dest


def prune_backups(backups_dir: Path, *, keep: int = KEEP_BACKUPS) -> int:
    """Delete all but the `keep` most recent backups. Returns how many went.

    Sorted by NAME rather than by mtime, and the two agree by construction: the
    name carries an ISO date, which sorts lexicographically. mtime would not
    survive a restore or a `cp -r` of the backup directory, and this must keep
    working on a directory someone has moved."""
    files = sorted(backups_dir.glob(f"{_STEM}*.db"))
    doomed = files[:-keep] if keep > 0 else files
    for path in doomed:
        path.unlink()
    return len(doomed)
