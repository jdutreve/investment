"""Inbox watcher — event-driven corpus ingestion (docs/TASKS.md Task 3.1).

ADR-002 says the laptop sleeps, so there is NO nightly cron: this is a polling
asyncio task (60s) plus a drain on startup that picks up whatever was deposited
while the Mac was off. Those two paths are the same code — `scan_once` — so a
file dropped during sleep is treated exactly like one dropped while running.

THE QUIET PERIOD is the non-obvious part. A batch of files copied into the
inbox does not appear atomically: a large PDF is visible on disk long before
its bytes finish landing, and ingesting it then yields a truncated or corrupt
document. So a batch is processed only once the NEWEST mtime in the inbox is
`inbox_quiet_seconds` (300) old. One still-settling file therefore holds back
the whole batch, which is deliberate — a coherent batch matters more than
latency on a job whose next consumer runs weekly.

FAILURE POLICY: one bad file must never kill the loop or block the queue
behind it. A file that fails ingestion moves to `inbox/failed/` and gets an
ErrorEvent naming the reason; the rest of the batch proceeds. This is the one
place in the system where an exception is caught broadly on purpose — the
alternative is a watcher that dies overnight on a malformed PDF and silently
stops ingesting.
"""

import asyncio
import contextlib
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from investment.corpus.ingester import (
    SUPPORTED_SUFFIXES,
    CorpusIngester,
    IngestResult,
    UnsupportedSourceError,
)
from investment.db.sqlite import InvestmentDB

logger = logging.getLogger(__name__)

POLL_SECONDS = 60.0
DEFAULT_QUIET_SECONDS = 300.0
FAILED_DIRNAME = "failed"

# Curator hook (docs/TASKS.md Task 3.1: "then invoke the curator if >=1 new
# Document was created"). Injected rather than imported so the watcher stays
# testable without an LLM and so the knowledge slice can land independently.
CuratorHook = Callable[[list[IngestResult]], Awaitable[None]]


@dataclass
class ScanReport:
    """What one scan did — returned for the CLI/tests, logged for ops."""

    ingested: list[IngestResult] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    deferred: bool = False  # batch still settling (quiet period not elapsed)

    @property
    def touched(self) -> bool:
        return bool(self.ingested or self.failed)


def pending_files(inbox: Path) -> list[Path]:
    """Files awaiting ingestion, oldest first.

    Skips the `failed/` quarantine and dotfiles (.DS_Store, partial-download
    artefacts like `.crdownload`), which are noise on macOS rather than
    deposits."""
    if not inbox.is_dir():
        return []
    files = [
        p
        for p in inbox.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.parent.name != FAILED_DIRNAME
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def batch_is_settled(files: list[Path], quiet_seconds: float, now: float | None = None) -> bool:
    """True once the newest file has been untouched for `quiet_seconds` — see
    the module docstring on why the whole batch waits for the slowest file."""
    if not files:
        return False
    newest = max(p.stat().st_mtime for p in files)
    return ((now if now is not None else time.time()) - newest) >= quiet_seconds


class InboxWatcher:
    def __init__(
        self,
        db: InvestmentDB,
        ingester: CorpusIngester,
        inbox: Path,
        sources: Path,
        *,
        quiet_seconds: float = DEFAULT_QUIET_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        curator: CuratorHook | None = None,
    ) -> None:
        self._db = db
        self._ingester = ingester
        self._inbox = inbox
        self._sources = sources
        self._quiet_seconds = quiet_seconds
        self._poll_seconds = poll_seconds
        self._curator = curator

    @property
    def failed_dir(self) -> Path:
        return self._inbox / FAILED_DIRNAME

    async def scan_once(self) -> ScanReport:
        """One pass: the startup drain and every poll tick both call this."""
        report = ScanReport()
        files = pending_files(self._inbox)
        if not files:
            return report
        if not batch_is_settled(files, self._quiet_seconds):
            logger.debug("inbox: %d file(s) still settling", len(files))
            report.deferred = True
            return report

        for path in files:
            try:
                result = await self._ingester.ingest_file(path)
            except Exception as exc:  # see module docstring: quarantine, never crash
                # Broad ON PURPOSE: a malformed PDF must quarantine itself, not
                # stop the watcher. The reason is recorded, never swallowed.
                reason = f"{type(exc).__name__}: {exc}"
                logger.warning("inbox: %s failed — %s", path.name, reason)
                await self._quarantine(path, reason)
                report.failed.append((path, reason))
                continue
            self._move_to_sources(path)
            report.ingested.append(result)
            logger.info("inbox: ingested %s (%d passages)", result.title, result.chunk_count)

        if report.ingested and self._curator is not None:
            await self._curator(report.ingested)
        return report

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until `stop` is set. The caller owns the task handle
        (CLAUDE.md: no fire-and-forget) and sets the event on SIGTERM/SIGINT.

        Waiting on `stop` with a timeout — rather than `asyncio.sleep` — is
        what makes shutdown immediate instead of up to a poll interval late."""
        logger.info("inbox watcher: draining %s at startup", self._inbox)
        await self._scan_guarded()
        while not stop.is_set():
            # Timeout IS the normal path — it means the poll interval
            # elapsed without a stop request.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
            if stop.is_set():
                break
            await self._scan_guarded()
        logger.info("inbox watcher: stopped")

    async def _scan_guarded(self) -> None:
        """A failure INSIDE scan_once (DB down, disk full) must not end the
        loop either — per-file errors are already handled one level down."""
        try:
            await self.scan_once()
        except Exception:
            logger.exception("inbox watcher: scan failed, continuing")

    async def _quarantine(self, path: Path, reason: str) -> None:
        self.failed_dir.mkdir(parents=True, exist_ok=True)
        await self._db.append_event(
            "ErrorEvent",
            source_uc="UC4",
            source_id=path.name,
            payload={"stage": "ingestion", "file": path.name, "reason": reason},
        )
        shutil.move(str(path), str(self._unique_target(self.failed_dir / path.name)))

    def _move_to_sources(self, path: Path) -> None:
        """Processed files leave the inbox, which is what makes the watcher
        idempotent: the queue IS the inbox, so an ingested file cannot be
        picked up twice."""
        target_dir = self._sources / ("corpus" if path.suffix.lower() in SUPPORTED_SUFFIXES else "")
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(self._unique_target(target_dir / path.name)))

    @staticmethod
    def _unique_target(target: Path) -> Path:
        """Never overwrite an existing archived file — re-depositing a book
        under the same name must not destroy the earlier copy (the DOCUMENT is
        deduplicated by title in the ingester; the FILE is kept as evidence)."""
        if not target.exists():
            return target
        for n in range(1, 1000):
            candidate = target.with_name(f"{target.stem}-{n}{target.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"cannot find a free name for {target}")


__all__ = [
    "InboxWatcher",
    "ScanReport",
    "UnsupportedSourceError",
    "batch_is_settled",
    "pending_files",
]
