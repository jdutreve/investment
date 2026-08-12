"""Get the message to the owner — by whatever channel works.

THE MESSAGE IS THE DELIVERABLE, the channel is an implementation detail. A week
of work ends in one digest carrying the month's order; a `needs-user-input` flag
is a question that has to reach a human. Until now Telegram was the only way
out, so a bad token meant the digest was rendered, logged as undelivered, and
dropped — the owner losing the whole week's output to a credential.

TWO CHANNELS, TRIED IN ORDER:

  1. TELEGRAM — the intended one. It reaches a phone, which is where the owner
     actually is on a chain morning.
  2. A LOCAL FILE, plus the full text in the log. Not a queue and not a retry:
     the message is written down where it can be read, and that is the whole
     promise. `~/data/investment/outbox/` sits beside `backups/`, derived from
     `db_path` exactly as those are, so it needs no new configuration.

WHY THE LOG CARRIES THE TEXT TOO, in full. Under launchd the log IS the
terminal (`deploy/`), and a line saying "written to outbox/2026-08-12.txt" asks
the owner to go and look; a line carrying the digest itself is read where they
already are. The duplication costs a few kilobytes a week.

WHAT THIS IS NOT: a store-and-forward outbox. Nothing re-sends the file when the
token is fixed, and nothing needs to — the digest renders from committed rows
and the market-signal block from the JOURNAL, so the next digest that gets
through still carries the decision. The file is the record of a message that
existed, not a message waiting to be sent.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from telegram.error import TelegramError

from investment.telegram.notify import MAX_MESSAGE_CHARS, send_message

logger = logging.getLogger(__name__)

Channel = Literal["telegram", "file"]

# What the "(n/m) " prefix can cost a part, at the sizes this ever reaches.
_PREFIX_ROOM = 12
_TRUNCATION_MARK = "\n\n… (truncated — the full text is in the outbox)"


def outbox_path(db_path: Path) -> Path:
    """`outbox/` beside the database, like `backups/` — derived, not configured."""
    return db_path.parent / "outbox"


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """`text` as messages that fit, split on BLANK LINES and never mid-sentence.

    THE DIGEST OUTGREW THE CHANNEL and nobody noticed until it was measured
    (2026-08-12): 7639 characters against Telegram's 4096, so `clip` was
    dropping 46% of it — the stack's standing, the recurring critiques, the
    scoreboard, the defender block, and the Worker's reading cut in the middle
    of a sentence. A truncation marker is honest about the loss but does not
    prevent it, and the lost half is not decoration.

    Blank lines are where the digest's own blocks meet (`render_digest` joins
    them with an empty line between), so packing whole paragraphs keeps every
    section intact. A single paragraph longer than the limit is clipped — the
    Worker's prose is the one thing that can reach that size, and cutting it is
    strictly better than losing the six sections behind it.

    Numbered when there is more than one part, so a message that never arrives
    is visible as a hole rather than as an ending."""
    if len(text) <= limit:
        return [text]
    blocks = text.split("\n\n")
    parts: list[str] = []
    current = ""
    # Room for the "(n/m) " prefix added below — counted BEFORE packing, because
    # a part packed to exactly the limit would overflow the moment it is
    # numbered.
    room = limit - _PREFIX_ROOM
    for block in blocks:
        block = block if len(block) <= room else clip_to(block, room)
        if not current:
            current = block
        elif len(current) + 2 + len(block) <= room:
            current = f"{current}\n\n{block}"
        else:
            parts.append(current)
            current = block
    if current:
        parts.append(current)
    return [f"({n}/{len(parts)}) {p}" for n, p in enumerate(parts, 1)]


def clip_to(text: str, limit: int) -> str:
    """One block cut to `limit`, with the loss visible."""
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_MARK)] + _TRUNCATION_MARK


async def deliver(text: str, *, token: str, chat_id: str, outbox: Path) -> Channel:
    """Send `text` to the owner. Returns the channel that carried it.

    NEVER RAISES. Delivery is the last step of whatever it reports on — a chain
    that aborted, a month that decided — and taking the caller down here would
    replace a reported failure with an unreported one. The file fallback means
    there is no failure mode left to report anyway: the worst case is that the
    message is on disk instead of on a phone."""
    try:
        # Several messages when the text outgrows the channel, in order. Any
        # failure aborts the rest: half a digest on the phone and the whole of
        # it in the outbox is better than a phone holding parts 1 and 3.
        for part in split_message(text):
            await send_message(token, chat_id, part)
    except TelegramError as exc:
        path = write_locally(text, outbox)
        # WARNING, not ERROR: the message was delivered, just not where it was
        # meant to go. An ERROR here would cry wolf every week on a machine
        # whose owner has decided not to use Telegram at all.
        logger.warning(
            "TELEGRAM UNAVAILABLE (%s: %s) — written to %s instead:\n%s",
            type(exc).__name__,
            exc,
            path,
            text,
        )
        return "file"
    return "telegram"


def write_locally(text: str, outbox: Path) -> Path:
    """One file per message, named for the second it was produced, and NEVER
    overwriting an existing one.

    Not one file per DAY as the backups are: two messages can land in the same
    morning — the digest, and an event watch flagging something it refused to
    guess at — and the second must not overwrite the first.

    Seconds turned out not to be enough, which a test caught before any run did:
    the chain writes those two messages microseconds apart when Telegram is
    down, since both fail fast on the same rejected token. So a taken name gets
    a suffix rather than a finer clock — a resolution is a bet on how fast the
    caller is, and this is an answer. A message that silently overwrote another
    would be exactly the loss this fallback exists to prevent."""
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = outbox / f"{stamp}.txt"
    attempt = 2
    while path.exists():
        path = outbox / f"{stamp}-{attempt}.txt"
        attempt += 1
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path
