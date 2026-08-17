"""The Gmail draft channel (`gmail/draft.py`).

NEVER RAISES is the property under test throughout, mirroring
`test_delivery.py`'s own framing: this is an ADDITIONAL, best-effort third
channel bolted onto the weekly chain's `digest` step, and a bad app password
or a network hiccup here must cost a log line, never the rest of the chain.

No real IMAP connection anywhere — `imaplib.IMAP4_SSL` is monkeypatched with a
fake that records what was sent, the same style `test_delivery.py` uses for
`send_message`.
"""

import email
import imaplib
import logging
from typing import Any

import pytest

from investment.gmail import draft

SUBJECT = "Investment digest — 2026-08-16"
TEXT = "plain text body"
HTML = "<div><p>html body</p></div>"


class _FakeImap:
    """Records every call; `list`/`login`/`append` return canned responses."""

    def __init__(
        self,
        *,
        mailboxes: list[bytes] | None = None,
        login_ok: bool = True,
        append_status: str = "OK",
    ) -> None:
        self.mailboxes = (
            mailboxes
            if mailboxes is not None
            else [b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"']
        )
        self.login_ok = login_ok
        self.append_status = append_status
        self.calls: list[tuple[str, Any]] = []

    def login(self, address: str, app_password: str) -> None:
        self.calls.append(("login", (address, app_password)))
        if not self.login_ok:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

    def list(self) -> tuple[str, list[bytes]]:
        self.calls.append(("list", None))
        return "OK", self.mailboxes

    def append(self, folder: str, flags: str, date_time: Any, message: bytes) -> tuple[str, Any]:
        self.calls.append(("append", (folder, flags, message)))
        return self.append_status, [b"OK"]

    def __enter__(self) -> "_FakeImap":
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append(("close", None))


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeImap) -> None:
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, timeout=None: fake)


def _decoded_parts(message: bytes) -> list[str]:
    """Each MIME part's payload, DECODED — `MIMEText(..., "utf-8")` base64- or
    quoted-printable-encodes its body, so asserting on the raw bytes (as a
    naive test would) checks the wire encoding, not the content."""
    parsed = email.message_from_bytes(message)
    parts = []
    for part in parsed.walk():
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        parts.append(payload.decode("utf-8"))
    return parts


def test_a_working_login_appends_a_draft_flagged_message(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeImap()
    _install(monkeypatch, fake)

    ok = draft.create_draft(
        address="owner@gmail.com",
        app_password="app-pw-16-chars-x",
        subject=SUBJECT,
        text=TEXT,
        html=HTML,
    )

    assert ok is True
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["login", "list", "append", "close"]
    folder, flags, message = fake.calls[2][1]
    assert folder == '"[Gmail]/Drafts"'
    assert flags == r"(\Draft)"
    parts = _decoded_parts(message)
    assert TEXT in parts
    assert HTML in parts


def test_the_drafts_folder_is_found_by_its_special_use_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gmail names the mailbox differently by account locale
    ("[Google Mail]/Drafts" for some UK accounts) — found by the `\\Drafts`
    RFC 6154 attribute, never assumed by name."""
    fake = _FakeImap(
        mailboxes=[
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Drafts) "/" "[Google Mail]/Drafts"',
        ]
    )
    _install(monkeypatch, fake)

    draft.create_draft(
        address="a@gmail.com",
        app_password="app-pw-16-chars-x",
        subject=SUBJECT,
        text=TEXT,
        html=HTML,
    )

    folder, _flags, _message = fake.calls[2][1]
    assert folder == '"[Google Mail]/Drafts"'


def test_no_special_use_attribute_falls_back_to_the_common_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeImap(mailboxes=[b'(\\HasNoChildren) "/" "INBOX"'])
    _install(monkeypatch, fake)

    draft.create_draft(
        address="a@gmail.com",
        app_password="app-pw-16-chars-x",
        subject=SUBJECT,
        text=TEXT,
        html=HTML,
    )

    folder, _flags, _message = fake.calls[2][1]
    assert folder == draft._FALLBACK_DRAFTS_FOLDER


def test_a_rejected_app_password_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _FakeImap(login_ok=False)
    _install(monkeypatch, fake)

    with caplog.at_level(logging.WARNING):
        ok = draft.create_draft(
            address="owner@gmail.com",
            app_password="wrong-password-16",
            subject=SUBJECT,
            text=TEXT,
            html=HTML,
        )

    assert ok is False
    assert "Gmail draft creation failed" in caplog.text


def test_the_app_password_is_redacted_from_the_failure_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An IMAP auth error can echo the credential that failed
    (`redact.py`'s own stated reason for existing) — this channel's failure
    log must not be the place a real app password ends up on disk."""
    from investment.redact import register_secret

    secret = "super-secret-app-password"
    register_secret(secret)

    class _EchoingImap(_FakeImap):
        def login(self, address: str, app_password: str) -> None:
            raise imaplib.IMAP4.error(f"AUTHENTICATIONFAILED: bad password {secret}")

    fake = _EchoingImap()
    _install(monkeypatch, fake)

    with caplog.at_level(logging.WARNING):
        draft.create_draft(
            address="owner@gmail.com", app_password=secret, subject=SUBJECT, text=TEXT, html=HTML
        )

    assert secret not in caplog.text
    assert "REDACTED" in caplog.text


def test_a_non_ok_append_status_is_reported_and_returns_false(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _FakeImap(append_status="NO")
    _install(monkeypatch, fake)

    with caplog.at_level(logging.WARNING):
        ok = draft.create_draft(
            address="a@gmail.com",
            app_password="app-pw-16-chars-x",
            subject=SUBJECT,
            text=TEXT,
            html=HTML,
        )

    assert ok is False
    assert "returned status NO" in caplog.text


def test_a_connection_error_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(host: str, timeout: float | None = None) -> _FakeImap:
        raise OSError("Network is unreachable")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _boom)

    # No pytest.raises: the assertion is that this line returns at all.
    assert (
        draft.create_draft(
            address="a@gmail.com",
            app_password="app-pw-16-chars-x",
            subject=SUBJECT,
            text=TEXT,
            html=HTML,
        )
        is False
    )


def test_the_message_is_multipart_alternative_plain_before_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RFC 2046 8.5: a multipart/alternative's parts go least to most
    faithful, and a client renders the LAST one it can handle — plain text
    must come before HTML, not after."""
    fake = _FakeImap()
    _install(monkeypatch, fake)

    draft.create_draft(
        address="a@gmail.com",
        app_password="app-pw-16-chars-x",
        subject=SUBJECT,
        text=TEXT,
        html=HTML,
    )

    _folder, _flags, message = fake.calls[2][1]
    content_types = [part.get_content_type() for part in email.message_from_bytes(message).walk()]
    assert content_types.index("text/plain") < content_types.index("text/html")
