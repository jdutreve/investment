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


# -- a message that outgrows the channel ------------------------------------


def test_a_message_that_fits_is_left_alone() -> None:
    assert delivery.split_message("short") == ["short"]


def test_a_long_message_is_split_on_BLANK_LINES_not_mid_sentence() -> None:
    """THE DEFECT THIS FIXES, measured 2026-08-12: the digest had grown to 7639
    characters against Telegram's 4096, so `clip` was dropping 46% of it — the
    stack's standing, the recurring critiques, the scoreboard, the defender
    block, and the Worker's reading cut in the middle of a sentence."""
    blocks = [f"BLOCK{n} " + "x" * 300 for n in range(10)]
    parts = delivery.split_message("\n\n".join(blocks), limit=1000)

    assert len(parts) > 1
    rebuilt = "\n\n".join(p.split(") ", 1)[1] for p in parts)
    for n in range(10):
        assert f"BLOCK{n}" in rebuilt  # nothing lost
    assert all(len(p) <= 1000 for p in parts)


def test_every_part_is_numbered_so_a_missing_one_is_visible() -> None:
    """A message that never arrives must read as a hole, not as an ending."""
    parts = delivery.split_message("\n\n".join("y" * 400 for _ in range(6)), limit=1000)
    assert parts[0].startswith(f"(1/{len(parts)}) ")
    assert parts[-1].startswith(f"({len(parts)}/{len(parts)}) ")


def test_one_oversized_paragraph_is_clipped_rather_than_costing_the_others() -> None:
    """The Worker's prose is the one block that can exceed a whole message on
    its own. Cutting it is strictly better than losing the six sections behind
    it."""
    parts = delivery.split_message("A" * 3000 + "\n\nTAIL BLOCK", limit=1000)
    assert "truncated" in parts[0]
    assert "TAIL BLOCK" in parts[-1]  # the sections behind it survive


async def test_a_split_digest_goes_out_as_several_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []

    async def ok(token: str, chat_id: str, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(delivery, "send_message", ok)
    long_digest = "\n\n".join(f"section {n} " + "z" * 900 for n in range(8))

    assert (
        await delivery.deliver(long_digest, token="t", chat_id="c", outbox=tmp_path) == "telegram"
    )
    assert len(sent) > 1
    assert all(len(m) <= 4096 for m in sent)


async def test_a_failure_partway_through_puts_the_WHOLE_text_in_the_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half a digest on the phone and all of it in the outbox beats a phone
    holding parts 1 and 3."""
    calls = 0

    async def flaky(token: str, chat_id: str, text: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimedOut()

    monkeypatch.setattr(delivery, "send_message", flaky)
    long_digest = "\n\n".join(f"section {n} " + "z" * 900 for n in range(8))
    outbox = tmp_path / "outbox"

    assert await delivery.deliver(long_digest, token="t", chat_id="c", outbox=outbox) == "file"
    written = next(iter(outbox.glob("*.txt"))).read_text()
    for n in range(8):
        assert f"section {n}" in written


async def test_an_unwritable_outbox_does_not_take_the_caller_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`deliver` promises NEVER RAISES, and the promise used to hold only for
    the paths anyone had thought about: the fallback for a failed send could
    itself fail, on the one morning Telegram is already down."""

    async def down(token: str, chat_id: str, text: str) -> None:
        raise TimedOut()

    def unwritable(text: str, outbox: Path) -> Path:
        raise PermissionError("read-only file system")

    monkeypatch.setattr(delivery, "send_message", down)
    monkeypatch.setattr(delivery, "write_locally", unwritable)

    with caplog.at_level(logging.ERROR):
        assert (
            await delivery.deliver("the digest", token="t", chat_id="c", outbox=tmp_path) == "file"
        )
    # THE LOG IS THE LAST RESORT: the message still exists somewhere readable.
    assert "the digest" in caplog.text
    assert "unwritable" in caplog.text


async def test_a_non_telegram_failure_still_reaches_the_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Only `TelegramError` used to trigger the fallback, so a bug in this code
    — a bad type, a broken loop — propagated and lost the message entirely."""

    async def wrong(token: str, chat_id: str, text: str) -> None:
        raise TypeError("chat_id must be a string")

    monkeypatch.setattr(delivery, "send_message", wrong)
    outbox = tmp_path / "outbox"

    with caplog.at_level(logging.ERROR):
        assert await delivery.deliver("the digest", token="t", chat_id="c", outbox=outbox) == "file"
    assert next(iter(outbox.glob("*.txt"))).read_text().strip() == "the digest"
    # Named as the bug it is, rather than as a channel outage.
    assert "UNEXPECTEDLY" in caplog.text
