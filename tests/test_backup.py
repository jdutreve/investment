"""Online backup (`db/backup.py`, docs/TASKS.md Phase 7).

Against a REAL database with a real WAL, because that is the whole point: a
plain file copy of a WAL database can capture a torn state and restore as a
corrupt file, silently. The assertion that the copy OPENS AND READS is therefore
not ceremony — it is the property `sqlite3.backup` is chosen for.
"""

import sqlite3
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from investment.db import backup as backup_module
from investment.db.backup import KEEP_BACKUPS, backup_database, backup_path, prune_backups
from investment.db.sqlite import InvestmentDB

TODAY = date(2026, 8, 12)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "investment.db")
    await conn.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4seasons', 'Four Seasons', 1, 't', '2026-01-01')"
    )
    yield conn
    await conn.close()


async def test_the_backup_is_a_readable_copy_of_the_live_database(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """Taken while the agent's own connection is OPEN — the live case. The
    source is opened read-only, so this path cannot write to the live file even
    by accident."""
    backups = tmp_path / "backups"
    written = backup_database(tmp_path / "investment.db", backups, today=TODAY)

    assert written == backup_path(backups, TODAY)
    con = sqlite3.connect(f"file:{written}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id FROM framework").fetchall()
    finally:
        con.close()
    assert [r[0] for r in rows] == ["4seasons"]


async def test_the_days_file_is_overwritten_rather_than_multiplied(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """One file per DAY, not per run: an ingestion batch can fire several times
    an hour, and a per-run name would push a whole day of chain history out of
    the 14-file window in an afternoon."""
    backups = tmp_path / "backups"
    backup_database(tmp_path / "investment.db", backups, today=TODAY)
    await db.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('permanent', 'Permanent', 0, 't', '2026-01-01')"
    )
    backup_database(tmp_path / "investment.db", backups, today=TODAY)

    assert len(list(backups.glob("investment-*.db"))) == 1
    con = sqlite3.connect(f"file:{backup_path(backups, TODAY)}?mode=ro", uri=True)
    try:  # the LAST write of the day wins — the one a restore would want
        assert con.execute("SELECT count(*) FROM framework").fetchone()[0] == 2
    finally:
        con.close()


def test_pruning_keeps_the_most_recent_and_deletes_the_rest(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    days = [TODAY - timedelta(days=n) for n in range(KEEP_BACKUPS + 5)]
    for day in days:
        backup_path(backups, day).write_bytes(b"")

    assert prune_backups(backups) == 5
    survivors = sorted(p.name for p in backups.glob("investment-*.db"))
    assert len(survivors) == KEEP_BACKUPS
    assert backup_path(backups, TODAY).name in survivors
    assert backup_path(backups, days[-1]).name not in survivors  # the oldest went


def test_pruning_sorts_by_NAME_so_a_moved_directory_still_prunes(tmp_path: Path) -> None:
    """mtime would not survive a restore or a `cp -r` of the backup directory,
    and the name carries an ISO date, which sorts lexicographically. Written
    newest-first on purpose: under an mtime sort this would keep the wrong 14."""
    backups = tmp_path / "backups"
    backups.mkdir()
    for day in [TODAY - timedelta(days=n) for n in range(KEEP_BACKUPS + 3)]:
        backup_path(backups, day).write_bytes(b"")

    prune_backups(backups)
    survivors = sorted(p.name for p in backups.glob("investment-*.db"))
    assert survivors[-1] == backup_path(backups, TODAY).name
    assert len(survivors) == KEEP_BACKUPS


def test_an_empty_directory_prunes_nothing(tmp_path: Path) -> None:
    assert prune_backups(tmp_path) == 0


class _Proxy:
    """A stand-in for one of the two connections `backup_database` opens.

    `sqlite3.Connection` is an immutable type, so its methods cannot be
    monkeypatched; wrapping the connection is the only way to make the copy or
    its verification fail on demand. Everything not overridden is delegated, so
    the real backup still happens where the test wants it to."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


def _patch_connections(
    monkeypatch: pytest.MonkeyPatch, *, source: type[_Proxy] | None, target: type[_Proxy] | None
) -> None:
    """Wrap the read-only SOURCE, the staging TARGET, or both. The source is
    the one opened with `uri=True` (`mode=ro`), which is what tells them
    apart."""
    real_connect = sqlite3.connect

    def connect(path: object, *args: object, **kwargs: object) -> object:
        real = real_connect(path, *args, **kwargs)  # type: ignore[arg-type]
        wrapper = source if kwargs.get("uri") else target
        return wrapper(real) if wrapper else real

    monkeypatch.setattr(backup_module.sqlite3, "connect", connect)


async def test_an_interrupted_backup_leaves_THE_DAY_S_BACKUP_INTACT(
    db: InvestmentDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backing up straight onto the day's file destroyed a good copy to make a
    bad one. The second run of a day truncated the first, so a crash in those
    seconds left a half-written file under the name of the only backup — on
    exactly the kind of day someone would want to restore it."""
    db_path = tmp_path / "investment.db"
    backups = tmp_path / "backups"

    first = backup_database(db_path, backups, today=TODAY)
    good = first.read_bytes()
    assert good

    class Interrupted(_Proxy):
        def backup(self, target: object, **kw: object) -> None:
            raise KeyboardInterrupt("the laptop lid closed mid-copy")

    _patch_connections(monkeypatch, source=Interrupted, target=None)
    with pytest.raises(KeyboardInterrupt):
        backup_database(db_path, backups, today=TODAY)

    assert first.read_bytes() == good  # untouched
    assert list(backups.glob("*.partial")) == []  # and no debris left behind


async def test_a_backup_that_fails_its_integrity_check_is_not_published(
    db: InvestmentDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy that cannot be read is not a backup, so the check runs BEFORE the
    rename rather than being something a restore discovers.

    Real corruption rather than a faked verdict: the staging file is filled
    with a plausible header and rubbish, which is what a half-written copy
    looks like from the outside."""
    db_path = tmp_path / "investment.db"
    backups = tmp_path / "backups"
    staging = backups / f"{backup_path(backups, TODAY).name}.partial"

    class Corrupting(_Proxy):
        def backup(self, target: object, **kw: object) -> None:
            staging.write_bytes(b"SQLite format 3\x00" + b"\xff" * 8192)

    _patch_connections(monkeypatch, source=Corrupting, target=None)
    with pytest.raises(sqlite3.DatabaseError):
        backup_database(db_path, backups, today=TODAY)

    assert not backup_path(backups, TODAY).exists()
    assert list(backups.glob("*.partial")) == []
