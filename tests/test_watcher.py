"""M7 tests for `corpus/watcher.py` — real SQLite, real files, no mocks.

The behaviours pinned here are the ones that fail SILENTLY in production if
broken: a batch ingested while still copying, a bad file that stops the loop,
or a file ingested twice.
"""

import asyncio
import os
import time
from pathlib import Path

import pytest

from investment.corpus.embedding import InProcessEmbedder
from investment.corpus.ingester import CorpusIngester, IngestResult
from investment.corpus.watcher import (
    FAILED_DIRNAME,
    InboxWatcher,
    batch_is_settled,
    pending_files,
)
from investment.db.sqlite import InvestmentDB


@pytest.fixture(scope="module")
def embedder() -> InProcessEmbedder:
    return InProcessEmbedder("all-MiniLM-L6-v2")


@pytest.fixture
async def env(tmp_path: Path, embedder: InProcessEmbedder) -> tuple[InvestmentDB, InboxWatcher]:
    db = InvestmentDB(tmp_path / "t.db")
    inbox, sources = tmp_path / "inbox", tmp_path / "sources"
    inbox.mkdir()
    sources.mkdir()
    # quiet_seconds=0: the settling delay has its own dedicated tests below.
    watcher = InboxWatcher(
        db, CorpusIngester(db, embedder), inbox, sources, quiet_seconds=0.0, poll_seconds=0.01
    )
    return db, watcher


def _deposit(inbox: Path, name: str, text: str = "inflation erodes bonds. " * 60) -> Path:
    p = inbox / name
    p.write_text(text, encoding="utf-8")
    return p


# -- quiet period ----------------------------------------------------------


def test_batch_not_settled_while_a_file_is_still_landing(tmp_path: Path) -> None:
    # The failure this prevents: a 75MB PDF is visible on disk long before its
    # bytes finish copying, and ingesting it then yields a truncated document.
    f = tmp_path / "big.md"
    f.write_text("x")
    assert not batch_is_settled([f], quiet_seconds=300.0)


def test_batch_settles_once_quiet_elapses(tmp_path: Path) -> None:
    f = tmp_path / "old.md"
    f.write_text("x")
    old = time.time() - 400
    os.utime(f, (old, old))
    assert batch_is_settled([f], quiet_seconds=300.0)


def test_newest_file_holds_back_the_whole_batch(tmp_path: Path) -> None:
    # Deliberate: a coherent batch beats latency. One settling file defers all.
    old_file, new_file = tmp_path / "a.md", tmp_path / "b.md"
    old_file.write_text("x")
    new_file.write_text("x")
    stale = time.time() - 400
    os.utime(old_file, (stale, stale))
    assert not batch_is_settled([old_file, new_file], quiet_seconds=300.0)


async def test_scan_defers_and_ingests_nothing_while_settling(
    tmp_path: Path, embedder: InProcessEmbedder
) -> None:
    db = InvestmentDB(tmp_path / "t.db")
    inbox, sources = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    sources.mkdir()
    w = InboxWatcher(db, CorpusIngester(db, embedder), inbox, sources, quiet_seconds=300.0)
    _deposit(inbox, "fresh.md")
    report = await w.scan_once()
    assert report.deferred and not report.ingested
    assert len(await db.query("SELECT id FROM document")) == 0


# -- happy path ------------------------------------------------------------


async def test_scan_ingests_and_moves_out_of_inbox(
    env: tuple[InvestmentDB, InboxWatcher], tmp_path: Path
) -> None:
    db, w = env
    _deposit(tmp_path / "inbox", "Book One.md")
    report = await w.scan_once()
    assert len(report.ingested) == 1
    assert pending_files(tmp_path / "inbox") == []
    assert (tmp_path / "sources" / "corpus" / "Book One.md").exists()
    assert len(await db.query("SELECT id FROM document")) == 1


async def test_second_scan_is_a_no_op(
    env: tuple[InvestmentDB, InboxWatcher], tmp_path: Path
) -> None:
    # Idempotence comes from the queue BEING the inbox: an ingested file has
    # left it, so it cannot be processed twice.
    _, w = env
    _deposit(tmp_path / "inbox", "Once.md")
    await w.scan_once()
    assert not (await w.scan_once()).touched


async def test_curator_hook_fires_once_per_batch_with_results(
    tmp_path: Path, embedder: InProcessEmbedder
) -> None:
    db = InvestmentDB(tmp_path / "t.db")
    inbox, sources = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    sources.mkdir()
    seen: list[list[IngestResult]] = []

    async def curator(results: list[IngestResult]) -> None:
        seen.append(results)

    w = InboxWatcher(
        db, CorpusIngester(db, embedder), inbox, sources, quiet_seconds=0.0, curator=curator
    )
    _deposit(inbox, "A.md")
    _deposit(inbox, "B.md", "credit spreads widen in recessions. " * 60)
    await w.scan_once()
    assert len(seen) == 1 and len(seen[0]) == 2


async def test_curator_not_called_when_nothing_ingested(
    tmp_path: Path, embedder: InProcessEmbedder
) -> None:
    db = InvestmentDB(tmp_path / "t.db")
    inbox, sources = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    sources.mkdir()
    called = False

    async def curator(_: list[IngestResult]) -> None:
        nonlocal called
        called = True

    w = InboxWatcher(
        db, CorpusIngester(db, embedder), inbox, sources, quiet_seconds=0.0, curator=curator
    )
    await w.scan_once()
    assert not called


# -- failure policy --------------------------------------------------------


async def test_bad_file_is_quarantined_with_an_error_event(
    env: tuple[InvestmentDB, InboxWatcher], tmp_path: Path
) -> None:
    db, w = env
    (tmp_path / "inbox" / "notes.epub").write_text("nope")
    report = await w.scan_once()
    assert len(report.failed) == 1
    assert (tmp_path / "inbox" / FAILED_DIRNAME / "notes.epub").exists()
    events = await db.query("SELECT * FROM event_log WHERE type = 'ErrorEvent'")
    assert len(events) == 1 and "epub" in events[0]["payload"]


async def test_one_bad_file_does_not_block_the_rest_of_the_batch(
    env: tuple[InvestmentDB, InboxWatcher], tmp_path: Path
) -> None:
    # The production failure this prevents: a malformed deposit stalling the
    # queue behind it, so later books are never ingested.
    db, w = env
    inbox = tmp_path / "inbox"
    (inbox / "aaa-broken.epub").write_text("nope")
    _deposit(inbox, "zzz-good.md")
    report = await w.scan_once()
    assert len(report.failed) == 1 and len(report.ingested) == 1
    assert len(await db.query("SELECT id FROM document")) == 1


async def test_quarantine_does_not_overwrite_an_earlier_failure(
    env: tuple[InvestmentDB, InboxWatcher], tmp_path: Path
) -> None:
    _, w = env
    inbox = tmp_path / "inbox"
    for _ in range(2):
        (inbox / "same.epub").write_text("nope")
        await w.scan_once()
    failed = sorted(p.name for p in (inbox / FAILED_DIRNAME).iterdir())
    assert failed == ["same-1.epub", "same.epub"]


async def test_hidden_files_are_ignored(
    env: tuple[InvestmentDB, InboxWatcher], tmp_path: Path
) -> None:
    # .DS_Store and partial downloads are macOS noise, not deposits.
    _, w = env
    (tmp_path / "inbox" / ".DS_Store").write_text("x")
    assert not (await w.scan_once()).touched


# -- loop lifecycle --------------------------------------------------------


async def test_run_drains_at_startup_then_stops_promptly(
    env: tuple[InvestmentDB, InboxWatcher], tmp_path: Path
) -> None:
    # The startup drain is what picks up deposits made while the Mac slept
    # (ADR-002), and stop must not wait out a poll interval.
    db, w = env
    _deposit(tmp_path / "inbox", "Slept.md")
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert len(await db.query("SELECT id FROM document")) == 1
