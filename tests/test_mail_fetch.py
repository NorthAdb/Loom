"""The read-only fetch transport: the sanitized sample, a fixture-shaped double, and the redaction."""

import json
from pathlib import Path

from loom.core.config import Settings
from loom.tools.mail_fetch import (
    BODY_CHAR_CAP,
    ImapFetchTransport,
    _body_text,
    build_fetch_transport,
    redact_fetch_error,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mail" / "sanitized_inbox_sample.json"


class FakeFetchTransport:
    """Serves the sanitized sample; records which bodies were read."""

    def __init__(self, rows):
        self.rows = rows
        self.bodies_read = []

    def list_recent(self, limit: int = 20) -> "list[dict]":
        return [
            {key: row[key] for key in ("uid", "from_header", "subject", "date", "seen")}
            for row in self.rows[:limit]
        ]

    def fetch_body(self, uid: int) -> str:
        self.bodies_read.append(uid)
        return next(row["body"] for row in self.rows if row["uid"] == uid)


def load_sample() -> "list[dict]":
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_sanitized_sample_is_well_formed_and_synthetic():
    rows = load_sample()

    assert len(rows) == 5
    for row in rows:
        assert set(row) == {"uid", "from_header", "subject", "date", "body", "seen"}
        assert isinstance(row["uid"], int) and isinstance(row["seen"], bool)
        assert "@" in row["from_header"] and "example" in row["from_header"].split("@")[-1]
    bodies = " ".join(row["body"] for row in rows)
    assert "System note to the assistant" in bodies  # the hidden-instruction attempt is in the sample
    assert "you may send now" in bodies  # the send demand is in the sample


def test_fake_transport_over_the_sample_has_the_console_seam_shape():
    rows = load_sample()
    transport = FakeFetchTransport(rows)

    listed = transport.list_recent(limit=3)

    assert [row["uid"] for row in listed] == [row["uid"] for row in rows[:3]]
    assert set(listed[0]) == {"uid", "from_header", "subject", "date", "seen"}
    assert transport.fetch_body(listed[0]["uid"]) == rows[0]["body"]
    assert transport.bodies_read == [rows[0]["uid"]]


def test_redact_fetch_error_surfaces_scheme_host_only():
    leak = "LOGIN failed for owner with the real credentials inside"

    text = redact_fetch_error(RuntimeError(leak), "imap.example.com", 993)

    assert "imaps://imap.example.com:993" in text
    assert leak not in text and "credentials" not in text
    assert text.startswith("RuntimeError")


def test_redact_fetch_error_names_an_unset_host():
    assert "(unset host)" in redact_fetch_error(OSError("no route"), "", 993)


def test_body_cap_is_twenty_thousand_chars():
    assert BODY_CHAR_CAP == 20_000
    assert _body_text(("x" * (BODY_CHAR_CAP + 50)).encode("utf-8")) == "x" * BODY_CHAR_CAP
    assert _body_text("café ☕".encode()).startswith("café")


def test_build_fetch_transport_is_none_without_imap_host(tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")

    assert build_fetch_transport(settings) is None
    assert build_fetch_transport(settings.model_copy(update={"imap_host": "imap.example.com"})) is not None


class FakeImapConnection:
    """A fake IMAP session injected into the REAL ImapFetchTransport.

    Not a stand-in for the transport: the class under test drives this
    connection exactly as it would drive imaplib.IMAP4_SSL, so the search
    condition, the read-only flag, and the MIME parsing are all exercised.
    """

    def __init__(self, search_uids, messages):
        self.search_uids = search_uids
        self.messages = messages  # uid -> (flags, header_bytes, full_message_bytes)
        self.commands = []

    def login(self, user, password):
        self.commands.append(("login",))
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=False):
        self.commands.append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def uid(self, command, *args):
        self.commands.append(("uid", command, args))
        if command == "SEARCH":
            return "OK", [b" ".join(str(uid).encode() for uid in self.search_uids)]
        uid = int(args[0])
        flags, headers, full = self.messages[uid]
        if args[1].startswith("(FLAGS"):
            prefix = f"{uid} FETCH (FLAGS ({flags}) BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {{0}}".encode()
            return "OK", [(prefix, headers)]
        prefix = f"{uid} FETCH (BODY[] {{{len(full)}}}".encode()
        return "OK", [(prefix, full)]

    def logout(self):
        self.commands.append(("logout",))
        return "BYE", [b""]


def _rfc822(from_addr: str, subject: str, body: bytes) -> bytes:
    headers = f"From: {from_addr}\r\nSubject: {subject}\r\nDate: Tue, 4 Nov 2026 09:12:00 +0800\r\n".encode()
    return headers + body


def _fetch_settings(tmp_path):
    return Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")


def test_the_real_transport_lists_read_and_old_mail_not_only_recent(tmp_path):
    """P0-1: UID SEARCH ALL lists an old, already-read message alongside the new one."""
    old_mail = _rfc822("landlord@example.com", "October bill", b"Read weeks ago by another client.\r\n")
    new_mail = _rfc822("cousin@example.net", "Weekend", b"Arrived this morning.\r\n")
    connection = FakeImapConnection(
        search_uids=[5, 9],
        messages={5: ("", new_mail, new_mail), 9: (r"\Seen", old_mail, old_mail)},
    )
    transport = ImapFetchTransport(_fetch_settings(tmp_path), connection_factory=lambda: connection)

    rows = transport.list_recent()

    assert [row["uid"] for row in rows] == [5, 9]  # the old, read message is listed
    assert {row["from_header"] for row in rows} == {"landlord@example.com", "cousin@example.net"}
    assert rows[1]["seen"] is True
    searched = [cmd for cmd in connection.commands if cmd[0] == "uid" and cmd[1] == "SEARCH"]
    assert searched and searched[0][2][0] == "ALL"  # not RECENT
    examined = [cmd for cmd in connection.commands if cmd[0] == "select"]
    assert examined and examined[0][2] is True  # EXAMINE, read-only


def test_the_real_transport_extracts_plaintext_from_a_multipart_body(tmp_path):
    """P0-1: a multipart/alternative document yields its plaintext, never boundaries or base64."""
    mime = (
        b"Content-Type: multipart/alternative; boundary=loom-boundary\r\n\r\n"
        b"--loom-boundary\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        b"VISIBLE_PLAINTEXT_HEATING_BILL due on the 15th\r\n"
        b"--loom-boundary\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body><b>HTML_ONLY</b> version</body></html>\r\n"
        b"--loom-boundary--\r\n"
    )
    connection = FakeImapConnection(
        search_uids=[5], messages={5: ("", _rfc822("landlord@example.com", "bill", b""), mime)}
    )
    transport = ImapFetchTransport(_fetch_settings(tmp_path), connection_factory=lambda: connection)

    body = transport.fetch_body(5)

    assert "VISIBLE_PLAINTEXT_HEATING_BILL due on the 15th" in body
    assert "Content-Type" not in body and "loom-boundary" not in body and "HTML_ONLY" not in body


def test_the_real_transport_strips_html_when_there_is_no_plain_part(tmp_path):
    html_only = (
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body><p>Heating <b>bill</b> due soon.</p><script>alert(1)</script>"
        b"<style>p { color: red }</style></body></html>\r\n"
    )
    connection = FakeImapConnection(
        search_uids=[7], messages={7: ("", _rfc822("peer@example.com", "hi", b""), html_only)}
    )
    transport = ImapFetchTransport(_fetch_settings(tmp_path), connection_factory=lambda: connection)

    body = transport.fetch_body(7)

    assert "Heating bill due soon." in body
    assert "<" not in body and "alert(1)" not in body and "color: red" not in body


def test_the_real_transport_returns_empty_for_an_unreadable_body(tmp_path):
    binary_only = b"Content-Type: application/octet-stream\r\n\r\n\x00\x01\x02\r\n"
    connection = FakeImapConnection(
        search_uids=[3], messages={3: ("", _rfc822("bot@example.com", "blob", b""), binary_only)}
    )
    transport = ImapFetchTransport(_fetch_settings(tmp_path), connection_factory=lambda: connection)

    assert transport.fetch_body(3) == ""
