"""Read-only IMAP fetch: the real-mail transport feeding the inbound seam.

`ImapFetchTransport` is ADR-016's layer 1: it logs into the owner's mailbox
over IMAP4_SSL, EXAMINEs the INBOX (read-only), lists the most recent
messages regardless of their \\Recent or seen state (UID SEARCH ALL, the last
N uids), and reads each message as a full MIME document through BODY.PEEK, so
nothing on the server is marked or moved. The body a caller sees is the
message's readable text: the first text/plain part, or a tag-stripped
text/html part when the sender sent only HTML — never a raw MIME document.
The pinned verb set is login, logout, select/examine, uid, search, fetch —
no append, store, expunge, delete, or create exists here (a guard test pins
that over this file). Every failure is redacted to the scheme/host level with
no cause chain, in the style of tools/mail.py and tools/calendar.py. Fetched
bodies are data, never instructions, and the From header is never a principal.
"""

import imaplib
import re
from collections.abc import Callable
from contextlib import suppress
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from html import unescape
from typing import Protocol

from loom.core.config import Settings

BODY_CHAR_CAP = 20_000

_FETCH_HEADERS = "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
_FETCH_FULL = "(BODY.PEEK[])"

_TAG_BLOCKS = re.compile(r"(?is)<(script|style)\b.*?>.*?</\1>")
_TAGS = re.compile(r"(?s)<[^>]*>")
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")


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


def _part_text(part) -> str | None:
    """One MIME part's decoded text, or None when the content type is not readable text."""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError, ValueError):
        return None
    return content if isinstance(content, str) else None


def _strip_html(text: str) -> str:
    """Tag-stripped, entity-decoded, whitespace-collapsed text from an HTML part."""
    text = _TAG_BLOCKS.sub(" ", text)
    text = _TAGS.sub(" ", text)
    return _WHITESPACE.sub(" ", unescape(text)).strip()


def _readable_text(raw: bytes) -> str:
    """The message's readable text: the first text/plain part, else stripped text/html.

    The bytes are parsed as MIME first, so a multipart/alternative document
    yields its plaintext, never a boundary or base64. A message with no
    readable text part yields an empty string rather than raw MIME.
    """
    message = message_from_bytes(raw, policy=policy.default)
    plain: object = None
    html: object = None
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = part
        elif ctype == "text/html" and html is None:
            html = part
    if plain is not None:
        text = _part_text(plain)
        if text is not None:
            return text
    if html is not None:
        text = _part_text(html)
        if text is not None:
            return _strip_html(text)
    return ""


class ImapFetchTransport:
    """Read-only IMAP4_SSL INBOX reader: the most recent messages plus one readable body each."""

    def __init__(self, settings: Settings, *, connection_factory: Callable[[], object] | None = None) -> None:
        self._settings = settings
        self._connection_factory = connection_factory

    def list_recent(self, limit: int = 20) -> "list[dict]":
        """The most recent messages, oldest last: uid, from_header, subject, date, seen.

        UID SEARCH ALL covers read mail and mail other clients have already
        opened; \\Recent would hide both. Nothing is marked or moved.
        """
        if limit <= 0:
            return []
        client = self._connect()
        try:
            status, _data = client.select("INBOX", readonly=True)
            self._check(status, "examine the inbox")
            status, data = client.uid("SEARCH", "ALL")
            self._check(status, "search all messages")
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
        """One message's readable text, capped at BODY_CHAR_CAP characters.

        The full document arrives through BODY.PEEK and is MIME-parsed, so a
        multipart/alternative message yields its plaintext rather than its
        boundaries; an HTML-only message yields tag-stripped text.
        """
        client = self._connect()
        try:
            status, _data = client.select("INBOX", readonly=True)
            self._check(status, "examine the inbox")
            status, data = client.uid("FETCH", str(uid), _FETCH_FULL)
            self._check(status, "fetch the message")
            sections = [item[1] for item in data if isinstance(item, tuple)]
            return _body_text(sections[0]) if not sections else _readable_text(sections[0])[:BODY_CHAR_CAP]
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

    def _connect(self) -> object:
        if self._connection_factory is not None:
            return self._connection_factory()
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
