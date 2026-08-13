"""Strip secrets out of anything that leaves the process (CLAUDE.md "Dev
standards": "no secret in logs or EventLog payloads").

THE RULE WAS WRITTEN DOWN AND NOTHING ENFORCED IT, which is the shape of defect
this module exists to close. Two live paths carried a credential into a file on
disk:

  - TELEGRAM. `python-telegram-bot` builds `InvalidToken` from the token it was
    handed, and three call sites log that exception in full — `main._start_bot`,
    `delivery.deliver`, `telegram/notify.notify`. A placeholder token is a
    harmless string; a REAL token rejected because the bot was revoked writes
    itself into `agent.error.log`.
  - FRED. The api key is a QUERY PARAMETER (`market/fetcher.py`), and aiohttp's
    `ClientResponseError` carries the full request URL in its message. Every
    retry in `_with_retry` logs that exception, so one 400 from FRED prints the
    key three times.

Both then reach further than the log: `chain.run_chain` puts `str(exc)` in an
ErrorEvent payload, and the same text is sent to the owner over Telegram.

TWO MECHANISMS, because neither alone is enough:

  1. `RedactingFormatter` — installed by `main.configure_logging`, it scrubs the
     FORMATTED record, message and traceback together. A filter on the message
     would miss the traceback, which is exactly where `logger.exception` puts
     the credential. This one needs no call site to remember anything, so it
     also covers log lines nobody has written yet.
  2. `redact()` — called explicitly where text crosses into a store or a
     channel: EventLog payloads, chain error strings, quarantine reasons.

THE FORMATTER COVERS ONE PROCESS'S HANDLERS, which is why (2) exists at all.
`main.configure_logging` installs it on the root logger's two handlers; a caller
that configures logging itself — a test harness, a notebook, a future entry
point — gets none of it. So the call sites that are GUARANTEED to carry a
credential (`main._start_bot`, `telegram/notify`, `chain.run_chain`) redact
their own text and do not rely on the net. The formatter is what catches the
line nobody anticipated.

WHAT IS REDACTED: the exact secret VALUES registered from `Settings` (the
reliable half — no pattern can be wrong about a string it was handed), plus
patterns for the shapes a credential takes when it did not come from our own
config (a token in a URL a library built, a `Bearer` header). Registration
happens in `Settings.model_post_init`, so constructing the settings is what arms
this — no startup step to forget (CLAUDE.md "Ask when in doubt" / structure over
convention).

The registry is process-global and additive. It is never read back, only used to
build a replacement, so the only thing it leaks into is the redaction itself.
"""

import logging
import re

MASK = "***REDACTED***"

# Below this, a "secret" is not distinctive enough to substitute blindly: a
# 4-character value would rewrite ordinary prose. Every real credential this
# project holds (OpenRouter, FRED, Telegram) is far longer.
_MIN_SECRET_CHARS = 8

_secrets: set[str] = set()

# The shapes a credential takes when it did NOT come from our own settings —
# a library that built the URL, a header, a provider whose key we never see.
#
# PUBLIC because a second consumer arrived: `deploy/scan_secrets.py` is the
# pre-commit gate, and it must refuse to commit the shapes this refuses to log.
# Two lists would drift the first time one of them learned something.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Telegram bot token: <numeric bot id>:<35-char secret>. Matched first
    # because it appears bare in `InvalidToken`, outside any URL.
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}"),
    # Any credential-ish query parameter, in a URL or a repr of one.
    re.compile(r"(?i)\b(api_key|apikey|access_token|auth_token|token|key)=[^&\s'\"<>]+"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"),
)

# What each pattern keeps: the NAME of the parameter survives, its value does
# not. "api_key=***REDACTED***" still says which credential failed, which is the
# whole diagnostic value of the line.
_REPLACEMENTS: tuple[str, ...] = (MASK, rf"\1={MASK}", rf"\1 {MASK}")


def register_secret(value: str | None) -> None:
    """Add one value to the set `redact` substitutes. Idempotent; ignores
    absent or too-short values so an unset optional key is not a special case
    at the call site."""
    if value and len(value) >= _MIN_SECRET_CHARS:
        _secrets.add(value)


def redact(text: str) -> str:
    """`text` with every known secret and credential shape masked.

    Registered VALUES go first: they are exact, so a key that also happens to
    match a pattern is replaced once rather than half-matched by both."""
    for secret in _secrets:
        text = text.replace(secret, MASK)
    for pattern, replacement in zip(SECRET_PATTERNS, _REPLACEMENTS, strict=True):
        text = pattern.sub(replacement, text)
    return text


def redact_exception(exc: BaseException) -> str:
    """`str(exc)` fit to be stored or shown. The one-liner used wherever an
    exception's text lands in an EventLog payload or a Telegram message —
    named rather than inlined so those call sites read as a decision."""
    return redact(str(exc))


class RedactingFormatter(logging.Formatter):
    """A `Formatter` that masks secrets in the FINAL string.

    Subclassing the formatter rather than adding a `logging.Filter` is the
    load-bearing choice: a filter sees `record.msg` and `record.args` before
    formatting and cannot touch `exc_info`, so `logger.exception("fetch failed")`
    would emit a clean message above a traceback carrying the credential in the
    frame that raised. `format()` is the one place message, arguments and
    traceback exist as one string."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
