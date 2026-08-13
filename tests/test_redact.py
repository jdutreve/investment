"""Secrets must not survive a trip through a log line or an EventLog payload
(src/investment/redact.py; CLAUDE.md "Dev standards": "no secret in logs or
EventLog payloads").

NEGATIVE TESTS, deliberately: each one asserts that a known credential is
ABSENT from the output, not that the redaction produced some particular string.
A test that pinned the mask would keep passing if the mask were applied to the
wrong half of the line."""

import logging
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from investment import chain, redact
from investment.db.sqlite import InvestmentDB

# Shaped like the real things, so the patterns are exercised as they will be —
# which means the pre-commit secret scan sees them as credentials too, and it is
# right to. The marker is how a fixture says so out loud (deploy/scan_secrets.py).
TELEGRAM_TOKEN = "8123456789:AAF-3kQwErTyUiOpAsDfGhJkLzXcVbNm123"  # pragma: allowlist secret
FRED_KEY = "abcdef0123456789abcdef0123456789"  # pragma: allowlist secret


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "redact.db")
    yield conn
    await conn.close()


def test_a_telegram_token_is_masked_without_being_registered() -> None:
    """`InvalidToken` carries the token python-telegram-bot rejected, and the
    process that rejected it may not be the one holding it in settings."""
    text = f"InvalidToken: The token `{TELEGRAM_TOKEN}` was rejected by the server"
    out = redact.redact(text)
    assert TELEGRAM_TOKEN not in out
    assert "InvalidToken" in out  # the diagnosis survives


def test_an_api_key_query_parameter_is_masked_and_named() -> None:
    """aiohttp's ClientResponseError carries the full request URL, and the FRED
    key is a query parameter (market/fetcher.py)."""
    url = (
        "400, message='Bad Request', url='https://api.stlouisfed.org/fred/series/observations"
        f"?series_id=INDPRO&api_key={FRED_KEY}&file_type=json'"
    )
    out = redact.redact(url)
    assert FRED_KEY not in out
    assert "api_key=" in out  # which credential failed is still readable
    assert "series_id=INDPRO" in out  # the rest of the diagnosis is untouched


def test_a_registered_secret_is_masked_in_any_shape() -> None:
    """The reliable half: a value handed to the redactor cannot be missed by a
    pattern that did not anticipate where it would appear."""
    redact.register_secret("sk-or-v1-not-a-real-key-9f3a")
    assert "sk-or-v1-not-a-real-key-9f3a" not in redact.redact(
        "openrouter said: bad credentials for sk-or-v1-not-a-real-key-9f3a"
    )


def test_a_short_value_is_not_registered() -> None:
    """Substituting a 4-character 'secret' would rewrite ordinary prose."""
    redact.register_secret("abc")
    assert (
        redact.redact("abc is a fine start to an alphabet") == "abc is a fine start to an alphabet"
    )


def test_the_formatter_redacts_the_traceback_not_only_the_message() -> None:
    """WHY IT IS A FORMATTER AND NOT A FILTER: `logger.exception` puts the
    credential in `exc_info`, which a filter never sees."""
    formatter = redact.RedactingFormatter("%(message)s")
    try:
        raise ValueError(f"rejected token {TELEGRAM_TOKEN}")
    except ValueError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "fetch failed", (), sys.exc_info()
        )
    out = formatter.format(record)
    assert TELEGRAM_TOKEN not in out
    assert "fetch failed" in out and "ValueError" in out


async def test_a_chain_error_event_stores_no_credential(db: InvestmentDB) -> None:
    """The whole path: a step raises with a key in the message, and the
    append-only payload — which nothing can rewrite later — must not hold it."""

    async def boom() -> None:
        raise RuntimeError(f"Cannot connect to https://api.stlouisfed.org/fred?api_key={FRED_KEY}")

    result = await chain.run_chain(db, [("catch-up", boom)], "run-1")
    assert FRED_KEY not in (result.error or "")  # nor in the Telegram alert built from it

    rows = await db.query("SELECT payload FROM event_log WHERE type = 'ErrorEvent'")
    assert FRED_KEY not in str(rows[0]["payload"])
    assert "RuntimeError" in str(rows[0]["payload"])
