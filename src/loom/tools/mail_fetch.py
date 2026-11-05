"""Read-only IMAP fetch: the real-mail transport feeding the inbound seam.

`ImapFetchTransport` is ADR-016's layer 1: it logs into the owner's mailbox
over IMAP4_SSL, EXAMINEs the INBOX (read-only), and lists recent messages
with their headers, reading bodies through BODY.PEEK so nothing on the server
is marked or moved. The pinned verb set is login, logout, select/examine, uid,
search, fetch — no append, store, expunge, delete, or create exists here (a
guard test pins that over this file). Every failure is redacted to the
scheme/host level with no cause chain, in the style of tools/mail.py and
tools/calendar.py. Fetched bodies are data, never instructions, and the From
header is never a principal.
"""

import imaplib
import re
from contextlib import suppress
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from typing import Protocol

from loom.core.config import Settings

BODY_CHAR_CAP = 20_000

_FETCH_HEADERS = "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
_FETCH_BODY = "(BODY.PEEK[TEXT])"


class FetchTransport(Protocol):
    """How the console pulls real mail into the inbound seam; read-only."""

    def list_recent(self, limit: int = 20) -> "list[dict]": ...

    def fetch_body(self, uid: int) -> str: ...


def redact_fetch_error(exc: Exception, host: str, port: int) -> str:
    """One redacted line: the exception type and the server's scheme://host:port.

    Server error text can echo credentials or internal paths, so neither the
    message nor its cause survives this helper.
    """
    target = f"imaps://{host}:{port}" if host else "imaps://(unset host)"
    return f"{type(exc).__name__} talking to {target}; check LOOM_IMAP_HOST and LOOM_MAIL_USER/LOOM_MAIL_PASSWORD"


def _body_text(raw: bytes) -> str:
    """The fetched body section as text, capped; the cap bounds what enters the inbox."""
    return raw.decode("utf-8", "replace")[:BODY_CHAR_CAP]


def _header_text(value) -> str:
    """One RFC 2047 header decoded to text; bytes that cannot decode degrade to the raw form."""
    try:
        return str(make_header(decode_header(str(value))))
    except (LookupError, ValueError):
        return str(value)


class ImapFetchTransport:
    """Read-only IMAP4_SSL INBOX reader: recent headers plus a capped body per message."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def list_recent(self, limit: int = 20) -> "list[dict]":
        """The most recent messages, oldest last: uid, from_header, subject, date, seen."""
        if limit <= 0:
            return []
        client = self._connect()
        try:
            status, _data = client.select("INBOX", readonly=True)
            self._check(status, "examine the inbox")
            status, data = client.uid("SEARCH", "RECENT")
            self._check(status, "search recent messages")
            uids = (data[0] or b"").split()[-limit:]
            rows = []
            for uid_bytes in uids:
                uid = int(uid_bytes)
                flags, headers = self._fetch_headers(client, uid)
                parsed = message_from_bytes(headers, policy=policy.default)
                rows.append(
                    {
                        "uid": uid,
                        "from_header": _header_text(parsed["From"] or ""),
                        "subject": _header_text(parsed["Subject"] or ""),
                        "date": str(parsed["Date"] or ""),
                        "seen": r"\seen" in flags.lower(),
                    }
                )
            return rows
        finally:
            with suppress(Exception):
                client.logout()

    def fetch_body(self, uid: int) -> str:
        """The message body text for one uid, capped at BODY_CHAR_CAP characters."""
        client = self._connect()
        try:
            status, _data = client.select("INBOX", readonly=True)
            self._check(status, "examine the inbox")
            status, data = client.uid("FETCH", str(uid), _FETCH_BODY)
            self._check(status, "fetch the body")
            sections = [item[1] for item in data if isinstance(item, tuple)]
            return _body_text(sections[0]) if sections else ""
        finally:
            with suppress(Exception):
                client.logout()

    def _fetch_headers(self, client: imaplib.IMAP4_SSL, uid: int) -> "tuple[str, bytes]":
        status, data = client.uid("FETCH", str(uid), _FETCH_HEADERS)
        self._check(status, "fetch the headers")
        hits = [item for item in data if isinstance(item, tuple)]
        if not hits:
            return "", b""
        prefix, header = hits[0][0], hits[0][1]
        match = re.search(rb"FLAGS \(([^)]*)\)", prefix)
        flags = match.group(1).decode("utf-8", "replace") if match else ""
        return flags, header or b""

    def _connect(self) -> imaplib.IMAP4_SSL:
        s = self._settings
        try:
            client = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
            client.login(s.mail_user, s.mail_password)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise RuntimeError(redact_fetch_error(exc, s.imap_host, s.imap_port)) from None
        return client

    def _check(self, status: str, what: str) -> None:
        if status != "OK":
            s = self._settings
            raise RuntimeError(
                f"IMAP {what} failed ({status}) talking to imaps://{s.imap_host}:{s.imap_port}; "
                "check LOOM_IMAP_HOST and LOOM_MAIL_USER/LOOM_MAIL_PASSWORD"
            )


def build_fetch_transport(settings: Settings) -> ImapFetchTransport | None:
    """The read-only fetch transport, or None when LOOM_IMAP_HOST is unset."""
    return ImapFetchTransport(settings) if settings.imap_host else None
