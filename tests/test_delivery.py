"""Delivery (`delivery.py`).

The message is the deliverable and the channel is a detail. What these pin is
the property the fallback exists for: a week of work — a digest carrying the
month's order — must not be lost to a credential. Before the fallback it was:
rendered, logged as undelivered, dropped.
"""

import logging
from pathlib import Path

import pytest
from telegram.error import InvalidToken, TimedOut

from investment import delivery

DIGEST = "📊 Regime: stagflation\n🧭 Market-signal decision — IEF 40→0 | cash 0→40"


async def test_a_working_channel_carries_it_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, str]] = []

    async def ok(token: str, chat_id: str, text: str) -> None:
        sent.append((token, chat_id, text))

    monkeypatch.setattr(delivery, "send_message", ok)
    outbox = tmp_path / "outbox"

    channel = await delivery.deliver(DIGEST, token="t", chat_id="c", outbox=outbox)

    assert channel == "telegram"
    assert sent == [("t", "c", DIGEST)]
    assert not outbox.exists()  # no file when the intended channel worked


async def test_a_rejected_token_falls_back_to_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE CASE THIS EXISTS FOR, measured on the first real launch: `.env`
    carried the placeholder token. The digest is the week's output and it must
    end up somewhere readable."""

    async def rejected(token: str, chat_id: str, text: str) -> None:
        raise InvalidToken("The token `REPLACE_ME` was rejected by the server.")

    monkeypatch.setattr(delivery, "send_message", rejected)
    outbox = tmp_path / "outbox"

    channel = await delivery.deliver(DIGEST, token="REPLACE_ME", chat_id="c", outbox=outbox)

    assert channel == "file"
    written = list(outbox.glob("*.txt"))
    assert len(written) == 1
    assert written[0].read_text().strip() == DIGEST


async def test_a_network_failure_falls_back_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not only a bad token: a timeout on a chain morning loses the same
    digest, and the owner is no less entitled to read it."""

    async def timed_out(token: str, chat_id: str, text: str) -> None:
        raise TimedOut()

    monkeypatch.setattr(delivery, "send_message", timed_out)
    assert await delivery.deliver(DIGEST, token="t", chat_id="c", outbox=tmp_path / "o") == "file"


async def test_delivery_never_raises_on_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delivery is the LAST step of whatever it reports on — a chain that
    aborted, a month that decided. Raising here would replace a reported
    failure with an unreported one (the rule `telegram/notify.py` states)."""

    async def rejected(token: str, chat_id: str, text: str) -> None:
        raise InvalidToken("nope")

    monkeypatch.setattr(delivery, "send_message", rejected)
    # No `pytest.raises`: the assertion is that this line returns at all.
    assert await delivery.deliver("x", token="t", chat_id="c", outbox=tmp_path / "o") == "file"


async def test_the_full_text_reaches_the_log_as_well(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Under launchd the log IS the terminal. A line saying "written to
    outbox/…" asks the owner to go and look; a line carrying the digest is read
    where they already are."""

    async def rejected(token: str, chat_id: str, text: str) -> None:
        raise InvalidToken("nope")

    monkeypatch.setattr(delivery, "send_message", rejected)
    with caplog.at_level(logging.WARNING):
        await delivery.deliver(DIGEST, token="t", chat_id="c", outbox=tmp_path / "o")

    assert "IEF 40→0" in caplog.text
    assert "TELEGRAM UNAVAILABLE" in caplog.text


def test_two_messages_in_one_morning_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """The digest and an event watch flagging something it refused to guess at
    can both land on a weekly run, microseconds apart when Telegram is down: both
    fail fast on the same rejected token. A second-resolution name loses the
    first, which is the exact loss this fallback exists to prevent — the first
    version of this test tolerated it and was wrong to."""
    outbox = tmp_path / "outbox"
    first = delivery.write_locally("digest", outbox)
    second = delivery.write_locally("2 events need your input", outbox)

    assert first != second
    assert first.read_text().strip() == "digest"
    assert second.read_text().strip() == "2 events need your input"
    assert len(list(outbox.glob("*.txt"))) == 2


def test_the_outbox_sits_beside_the_database(tmp_path: Path) -> None:
    """Derived from `db_path` like `backups/`, so it needs no configuration and
    cannot drift from where the owner already looks."""
    assert delivery.outbox_path(tmp_path / "investment.db") == tmp_path / "outbox"
