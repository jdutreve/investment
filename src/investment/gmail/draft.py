"""Creates the weekly digest as a genuine Gmail DRAFT — the agent's own hand,
not the manual weekly gesture it replaces (owner, 2026-08-17: "genere
automatiquement... et mets le dans brouillon" reopened and reversed the
2026-08-17 "formalize the manual gesture" call from earlier the same day).

IMAP APPEND, not the Gmail REST API — deliberately (owner's choice between the
two, 2026-08-17): `imaplib` + `email` are stdlib, so this is the one channel in
`delivery.py`'s family that adds no dependency. It also means the content this
module builds is NOT run through whatever the Gmail draft-COMPOSITION REST API
does to a draft's HTML — `[[feedback-email-draft-never-send]]`'s `style=`-
stripping finding was diagnosed against THAT API; a raw MIME message APPENDed
to the Drafts mailbox is stored as sent, unprocessed. `gmail/render.py` still
avoids `style=` on principle (a draft opened, edited and re-saved in the Gmail
UI would round-trip through the composer and lose it then), but this channel
was never the one the finding was about.

NEVER RAISES — same contract as `delivery.deliver`. This is an ADDITIONAL,
best-effort third channel bolted onto the weekly chain's `digest` step, not a
replacement for Telegram/the local file: a bad app password or a network
hiccup here must not take the rest of the chain down with it."""

import imaplib
import logging
import re
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from investment.redact import redact_exception

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
# Used only when no mailbox reports the `\Drafts` SPECIAL-USE attribute (RFC
# 6154) — the common Gmail default, kept as a fallback rather than the primary
# lookup because the name varies by account locale ("[Google Mail]/Drafts").
_FALLBACK_DRAFTS_FOLDER = '"[Gmail]/Drafts"'
# `market/fetcher.py`'s `fetch_yahoo_series` had no timeout at all until
# 2026-08-16 and an 8-hour chain run was the cost of finding out — this
# channel gets one from the start rather than earning the lesson twice.
IMAP_TIMEOUT_SECONDS = 30.0


def _build_message(*, address: str, subject: str, text: str, html: str) -> bytes:
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = address
    message["To"] = address
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    # PLAIN FIRST, HTML SECOND — RFC 2046 8.5: a multipart/alternative's parts
    # go from least to most faithful, and a client picks the LAST one it can
    # render.
    message.attach(MIMEText(text, "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))
    return message.as_bytes()


# The IMAP LIST response is `(flags) "delimiter" name`, and `name` is a
# QUOTED string that can itself contain spaces ("[Google Mail]/Drafts") — a
# plain `rsplit(" ", 1)` truncates exactly those names at their first space.
_QUOTED_MAILBOX_NAME = re.compile(r'"([^"]*)"\s*$')


def _drafts_folder(imap: imaplib.IMAP4_SSL) -> str:
    """The mailbox name IMAP APPEND must target to land in Drafts, found by
    its `\\Drafts` attribute rather than assumed — Gmail's own name for it
    depends on the account's language setting."""
    status, mailboxes = imap.list()
    if status == "OK" and mailboxes:
        for raw in mailboxes:
            line = raw.decode() if isinstance(raw, bytes) else str(raw)
            if "\\Drafts" not in line:
                continue
            match = _QUOTED_MAILBOX_NAME.search(line)
            if match:
                return f'"{match.group(1)}"'
            return line.rsplit(" ", 1)[-1].strip()  # an unquoted (literal) name
    return _FALLBACK_DRAFTS_FOLDER


def create_draft(*, address: str, app_password: str, subject: str, text: str, html: str) -> bool:
    """Append one message to the account's Drafts mailbox with the `\\Draft`
    flag set — a real draft, never sent, per the owner's standing instruction
    for anything composed on their behalf.

    Returns whether it worked. Never raises: caught broadly and logged as a
    WARNING (not an ERROR — a digest whose two other channels succeeded is not
    a chain failure), with the exception text redacted exactly as
    `delivery.deliver` redacts a Telegram failure — an IMAP auth error can
    echo the credential that failed."""
    message = _build_message(address=address, subject=subject, text=text, html=html)
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, timeout=IMAP_TIMEOUT_SECONDS) as imap:
            imap.login(address, app_password)
            folder = _drafts_folder(imap)
            status, _response = imap.append(
                folder, r"(\Draft)", imaplib.Time2Internaldate(time.time()), message
            )
        if status != "OK":
            logger.warning("Gmail draft APPEND to %s returned status %s", folder, status)
            return False
        logger.info("Gmail draft created in %s", folder)
        return True
    except (imaplib.IMAP4.error, OSError) as exc:
        logger.warning("Gmail draft creation failed: %s", redact_exception(exc))
        return False
