"""IMAP mail drafts: create and update only. There is no send path in this module.

`build_draft_message` is pure and returns RFC 5322 bytes. `ImapMailTransport`
APPENDs drafts to the configured folder with the \\Draft flag and replaces one
draft through a UID-only flow: UID SEARCH, a fetch-verified identity check,
append of the replacement, then UID STORE \\Deleted and UID EXPUNGE of that one
UID (the expunge step only when the server advertises UIDPLUS; otherwise the
old draft stays on the server and the result says so). Credentials come from
Settings, never from tool arguments, audit records, or error messages.
"""

import imaplib
import re
from contextlib import suppress
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
    if len(old_id) > 998:
        # RFC 5322 caps a header line at 998 characters, so a longer id cannot
        # be a real Message-ID and would truncate inside approval payloads.
        raise ValueError(
            f"draft id is longer than the 998-character Message-ID line limit ({len(old_id)} characters)"
        )


def _imap_quote(mailbox: str) -> str:
    """One RFC 3501 quoted string; real servers reject [Gmail]/Drafts unquoted."""
    escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _scrub(exc: Exception, password: str) -> str:
    """Server error text with the password removed, should it ever echo back."""
    text = f"{type(exc).__name__}: {exc}"
    if password:
        text = text.replace(password, "***")
    return text


class ImapMailTransport:
    """IMAP4_SSL draft store: append with \\Draft, replace = UID search + verified fetch + append."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def append_draft(self, message_bytes: bytes) -> str:
        client = self._connect()
        try:
            status, _data = client.append(_imap_quote(self._mailbox()), r"(\Draft)", None, message_bytes)
            self._check(status, "append draft")
            return _message_id(message_bytes)
        finally:
            client.logout()

    def replace_draft(self, old_id: str, message_bytes: bytes) -> str:
        """Replace one draft, touching only the message the id names.

        A header SEARCH matches substrings, so the fetch of the candidate is
        the ground truth: its Message-ID must equal `old_id` exactly and its
        FLAGS must carry \\Draft, or nothing on the server is modified. The
        replacement is appended before the old draft is marked \\Deleted, so a
        mid-flight failure leaves a recoverable duplicate, never a lost draft.
        """
        _validate_draft_id(old_id)
        client = self._connect()
        try:
            status, _ = client.select(_imap_quote(self._mailbox()))
            self._check(status, "open the drafts folder")
            status, data = client.uid("SEARCH", "HEADER", "Message-ID", f'"{old_id}"')
            self._check(status, "search the old draft")
            uids = (data[0] or b"").split()
            if len(uids) != 1:
                raise ValueError(
                    f"expected exactly one draft matching Message-ID {old_id}, found {len(uids)}; "
                    "nothing was modified"
                )
            uid = uids[0].decode("ascii")
            flags, fetched_id = self._fetch_old_draft(client, uid)
            if fetched_id != old_id:
                raise ValueError(
                    f"the draft at uid {uid} reports Message-ID {fetched_id or '(none)'}, not {old_id}; "
                    "nothing was modified"
                )
            if r"\draft" not in flags.lower():
                raise ValueError(f"the message at uid {uid} is not flagged \\Draft; nothing was modified")
            status, _ = client.append(_imap_quote(self._mailbox()), r"(\Draft)", None, message_bytes)
            self._check(status, "append the replacement draft")
            new_id = _message_id(message_bytes)
            status, _ = client.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
            if status != "OK":
                # The replacement is already on the server, so unlike the steps
                # before it this failure leaves two drafts behind; say so.
                raise RuntimeError(
                    f"IMAP marking the old draft deleted failed ({status}); the replacement draft was appended "
                    f"but the old draft {old_id} is not deleted, so the drafts folder now contains two copies"
                )
            if not any(str(cap).upper() == "UIDPLUS" for cap in client.capabilities):
                return f"{new_id} (the old draft is kept on the server: it does not advertise UIDPLUS)"
            status, _ = client.uid("EXPUNGE", uid)
            self._check(status, "expunge the old draft")
            return new_id
        finally:
            client.logout()

    def _fetch_old_draft(self, client: imaplib.IMAP4_SSL, uid: str) -> tuple[str, str]:
        status, data = client.uid("FETCH", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        self._check(status, "fetch the old draft")
        hits = [item for item in data if isinstance(item, tuple)]
        if len(hits) != 1:
            raise ValueError(
                f"could not read the draft at uid {uid}: fetch returned {len(hits)} message(s); nothing was modified"
            )
        prefix, header = hits[0][0], hits[0][1]
        match = re.search(rb"FLAGS \(([^)]*)\)", prefix)
        flags = match.group(1).decode("utf-8", "replace") if match else ""
        message_id = str(message_from_bytes(header or b"", policy=policy.default)["Message-ID"] or "")
        return flags, message_id

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
            ) from None
        try:
            client.login(s.mail_user, s.mail_password)
        except imaplib.IMAP4.error as exc:
            # A server that already disconnected makes logout raise, and that
            # error must not replace the redacted one (it can echo the password).
            with suppress(Exception):
                client.logout()
            raise RuntimeError(
                f"IMAP login failed ({_scrub(exc, s.mail_password)}); check LOOM_MAIL_USER and LOOM_MAIL_PASSWORD"
            ) from None
        return client

    def _check(self, status: str, what: str) -> None:
        if status != "OK":
            raise RuntimeError(f"IMAP {what} failed ({status}); check LOOM_IMAP_HOST, LOOM_MAIL_USER, and LOOM_MAIL_PASSWORD")
