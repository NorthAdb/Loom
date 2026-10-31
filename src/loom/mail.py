"""IMAP mail drafts: create and update only. There is no send path in this module.

`build_draft_message` is pure and returns RFC 5322 bytes. `ImapMailTransport`
APPENDs drafts to the configured folder with the \\Draft flag and replaces one
by searching the old draft's Message-ID, marking it \\Deleted, and expunging.
Credentials come from Settings, never from tool arguments, audit records, or
error messages.
"""

import imaplib
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

from loom.config import Settings

MAIL_NOT_CONFIGURED = "mail is not configured; set LOOM_IMAP_HOST and LOOM_MAIL_* in .env"


class MailTransport(Protocol):
    """How the mail tools store drafts; the id returned is the draft's handle."""

    def append_draft(self, message_bytes: bytes) -> str: ...

    def replace_draft(self, old_id: str, message_bytes: bytes) -> str: ...


def build_draft_message(to: str, subject: str, body: str, from_addr: str) -> bytes:
    """Render one draft message; the \\Draft flag is the transport's business."""
    if not to or "@" not in to:
        raise ValueError(f"to is not an email address: {to!r}")
    if not from_addr:
        raise ValueError("from address is empty; set LOOM_MAIL_USER")
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    return message.as_bytes()


def _message_id(message_bytes: bytes) -> str:
    """The draft id this module uses everywhere: the message's Message-ID."""
    parsed = message_from_bytes(message_bytes, policy=policy.default)
    return str(parsed["Message-ID"] or "")


def _validate_draft_id(old_id: str) -> None:
    """A draft id is a Message-ID like <...@...>; anything else never reaches IMAP."""
    if not (old_id.startswith("<") and old_id.endswith(">") and len(old_id) > 2):
        raise ValueError(f"draft id is not a Message-ID: {old_id!r}")
    if any(ch in old_id for ch in ('"', "\r", "\n")):
        raise ValueError(f"draft id contains forbidden characters: {old_id!r}")


def _scrub(exc: Exception, password: str) -> str:
    """Server error text with the password removed, should it ever echo back."""
    text = f"{type(exc).__name__}: {exc}"
    if password:
        text = text.replace(password, "***")
    return text


class ImapMailTransport:
    """IMAP4_SSL draft store: append with \\Draft, replace = search + delete + append."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def append_draft(self, message_bytes: bytes) -> str:
        client = self._connect()
        try:
            status, _data = client.append(self._mailbox(), r"(\Draft)", None, message_bytes)
            self._check(status, "append draft")
            return _message_id(message_bytes)
        finally:
            client.logout()

    def replace_draft(self, old_id: str, message_bytes: bytes) -> str:
        _validate_draft_id(old_id)
        client = self._connect()
        try:
            status, _ = client.select(self._mailbox())
            self._check(status, "open the drafts folder")
            status, data = client.search(None, "HEADER", "Message-ID", f'"{old_id}"')
            self._check(status, "search the old draft")
            matches = (data[0] or b"").split()
            if not matches:
                raise ValueError(f"no existing draft matches Message-ID {old_id}")
            # The replacement lands before the old draft is expunged: a failure
            # then leaves a recoverable duplicate, never a lost draft.
            status, _ = client.append(self._mailbox(), r"(\Draft)", None, message_bytes)
            self._check(status, "append the replacement draft")
            new_id = _message_id(message_bytes)
            for num in matches:
                client.store(num, "+FLAGS.SILENT", r"(\Deleted)")
            client.expunge()
            return new_id
        finally:
            client.logout()

    def _mailbox(self) -> str:
        return self._settings.mail_folder

    def _connect(self) -> imaplib.IMAP4_SSL:
        s = self._settings
        try:
            client = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
        except OSError as exc:
            raise RuntimeError(
                f"could not reach IMAP server {s.imap_host}:{s.imap_port} ({_scrub(exc, s.mail_password)}); "
                "check LOOM_IMAP_HOST and LOOM_IMAP_PORT"
            ) from exc
        try:
            client.login(s.mail_user, s.mail_password)
        except imaplib.IMAP4.error as exc:
            client.logout()
            raise RuntimeError(
                f"IMAP login failed ({_scrub(exc, s.mail_password)}); check LOOM_MAIL_USER and LOOM_MAIL_PASSWORD"
            ) from exc
        return client

    def _check(self, status: str, what: str) -> None:
        if status != "OK":
            raise RuntimeError(f"IMAP {what} failed ({status}); check LOOM_IMAP_HOST, LOOM_MAIL_USER, and LOOM_MAIL_PASSWORD")
