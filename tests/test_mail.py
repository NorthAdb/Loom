"""Mail module: message building, the tool surface, and IMAP error paths."""

import imaplib
import traceback
from email import message_from_bytes, policy

import pytest
from conftest import FakeMailTransport

from loom.config import ConfigError, Settings
from loom.mail import ImapMailTransport, _message_id, build_draft_message
from loom.tools import make_tools


def make_settings(tmp_path, **overrides):
    values = {
        "loom_env": "dev",
        "loom_data_dir": tmp_path,
        "loom_sandbox_root": tmp_path / "sandbox",
        "mail_user": "owner@example.com",
        "imap_host": "imap.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def make_registry(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    transport = FakeMailTransport()
    return make_tools(settings, {"mail": transport}), transport


def parsed(message_bytes):
    return message_from_bytes(message_bytes, policy=policy.default)


def test_build_draft_message_sets_headers_and_utf8_body():
    body = "会议安排：周三下午三点。\nPlease bring the notes."
    message_bytes = build_draft_message("peer@example.com", "周三的计划", body, "owner@example.com")

    message = parsed(message_bytes)
    assert message["From"] == "owner@example.com"
    assert message["To"] == "peer@example.com"
    assert message["Subject"] == "周三的计划"
    assert (message["Message-ID"] or "").startswith("<")
    text = message.get_body(preferencelist=("plain",)).get_content()
    assert text.strip() == body


def test_build_draft_message_rejects_bad_recipient_and_empty_from():
    with pytest.raises(ValueError, match="not an email address"):
        build_draft_message("not-an-address", "s", "b", "owner@example.com")
    with pytest.raises(ValueError, match="LOOM_MAIL_USER"):
        build_draft_message("peer@example.com", "s", "b", "")


def test_create_draft_tool_appends_once_and_reports_the_id(tmp_path):
    registry, transport = make_registry(tmp_path)

    result = registry["mail_create_draft"].func.invoke(
        {"to": "peer@example.com", "subject": "Plan", "body": "Lunch at noon?"}
    )

    assert len(transport.appended) == 1
    message = parsed(transport.appended[0])
    assert message["To"] == "peer@example.com"
    assert message["Subject"] == "Plan"
    assert "fake-draft-1" in result
    assert "Drafts" in result
    assert "not been sent" in result


def test_update_draft_tool_replaces_by_id(tmp_path):
    registry, transport = make_registry(tmp_path)

    result = registry["mail_update_draft"].func.invoke(
        {"draft_id": "old-7", "to": "peer@example.com", "subject": "Plan v2", "body": "Lunch at one?"}
    )

    assert transport.replaced[0][0] == "old-7"
    assert parsed(transport.replaced[0][1])["Subject"] == "Plan v2"
    assert "old-7" in result and "fake-draft-1" in result
    assert transport.appended == [], "update replaces; it never appends a second copy"


def test_mail_tools_without_a_host_are_inert_and_name_the_env_var(tmp_path):
    settings = make_settings(tmp_path, imap_host="")
    registry = make_tools(settings)

    with pytest.raises(RuntimeError, match="mail is not configured.*LOOM_IMAP_HOST"):
        registry["mail_create_draft"].func.invoke({"to": "a@b.com", "subject": "s", "body": "b"})
    with pytest.raises(RuntimeError, match="mail is not configured"):
        registry["mail_update_draft"].func.invoke({"draft_id": "d", "to": "a@b.com", "subject": "s", "body": "b"})


def test_imap_transport_connection_failure_names_the_host_var_without_the_password(tmp_path):
    settings = make_settings(tmp_path, imap_host="127.0.0.1", imap_port=1, mail_password="secret-pass-42")
    transport = ImapMailTransport(settings)

    with pytest.raises(RuntimeError, match="LOOM_IMAP_HOST") as exc_info:
        transport.append_draft(build_draft_message("a@b.com", "s", "b", "owner@example.com"))

    assert "secret-pass-42" not in str(exc_info.value)


class FakeIMAPClient:
    """Records every call so replace_draft's UID-only flow is observable.

    The uid() responses imitate real imaplib shapes: SEARCH returns one space
    separated uid line, FETCH returns a (response-prefix, header-literal) tuple
    followed by the closing paren. `messages` models the server's mailboxes:
    UID EXPUNGE drops just that uid, a bare EXPUNGE drops every \\Deleted one,
    so the tests can see which mail actually survived.
    """

    def __init__(
        self,
        search_hits=b"7",
        fetch_flags=r"(\Draft)",
        fetch_message_id=b"<old@loom>",
        store_status="OK",
        capabilities=("UIDPLUS",),
    ):
        self.calls = []
        self.search_hits = search_hits
        self.fetch_flags = fetch_flags
        self.fetch_message_id = fetch_message_id
        self.store_status = store_status
        self.capabilities = list(capabilities)
        self.messages = {"7": r"(\Draft)", "9": r"(\Deleted \Seen)"}

    def select(self, mailbox):
        self.calls.append(("select", mailbox))
        return "OK", [b"1"]

    def append(self, mailbox, flags, date, message_bytes):
        self.calls.append(("append", mailbox, flags))
        return "OK", [b""]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [self.search_hits]
        if command == "FETCH":
            header = b"Message-ID: " + self.fetch_message_id + b"\r\n\r\n"
            prefix = f"7 (UID 7 FLAGS {self.fetch_flags} BODY[HEADER.FIELDS (MESSAGE-ID)] {len(header)})".encode()
            return "OK", [(prefix, header), b")"]
        if command == "STORE":
            return self.store_status, [b""]
        if command == "EXPUNGE":
            self.messages.pop(args[0], None)
            return "OK", [args[0]]
        return "BAD", [b"unexpected uid command"]

    def expunge(self):
        self.calls.append(("expunge",))
        self.messages = {
            uid: flags for uid, flags in self.messages.items() if r"\deleted" not in flags.lower()
        }
        return "OK", [b""]

    def login(self, user, password):
        self.calls.append(("login", user))
        return "OK", [b"logged in"]

    def logout(self):
        self.calls.append(("logout",))
        return "OK", [b"Bye"]


def _patched_imap(monkeypatch, client):
    monkeypatch.setattr("loom.mail.imaplib.IMAP4_SSL", lambda host, port: client)


def _writes(client):
    """The calls that would change server state, in order."""
    return [
        call
        for call in client.calls
        if call[0] == "append"
        or call[0] in ("store", "expunge")
        or (call[0] == "uid" and call[1] in ("STORE", "EXPUNGE"))
    ]


def test_replace_draft_uid_flow_appends_then_stores_then_expunges_that_uid(monkeypatch, tmp_path):
    client = FakeIMAPClient(search_hits=b"7")
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    new_id = transport.replace_draft("<old@loom>", message)

    assert new_id == _message_id(message)
    assert ("select", '"Drafts"') in client.calls, "the mailbox name must be RFC 3501 quoted"
    assert ("append", '"Drafts"', r"(\Draft)") in client.calls
    assert ("uid", "STORE", "7", "+FLAGS.SILENT", r"(\Deleted)") in client.calls
    assert ("uid", "EXPUNGE", "7") in client.calls
    order = [call[0] for call in client.calls]
    assert order.index("append") < order.index("logout")
    assert "expunge" not in order, "bare EXPUNGE would wipe unrelated deleted mail"


def test_replace_draft_with_multiple_hits_refuses_without_modification(monkeypatch, tmp_path):
    client = FakeIMAPClient(search_hits=b"7 9")
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    with pytest.raises(ValueError, match="exactly one"):
        transport.replace_draft("<old@loom>", message)

    assert _writes(client) == [], "an ambiguous search must modify nothing"


def test_replace_draft_refuses_when_flags_lack_draft(monkeypatch, tmp_path):
    client = FakeIMAPClient(fetch_flags=r"(\Seen)")
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    with pytest.raises(ValueError, match="Draft"):
        transport.replace_draft("<old@loom>", message)

    assert _writes(client) == [], "a non-draft message must never be deleted"


def test_replace_draft_refuses_when_fetched_message_id_differs(monkeypatch, tmp_path):
    client = FakeIMAPClient(fetch_message_id=b"<other@loom>")
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    with pytest.raises(ValueError, match="not <old@loom>"):
        transport.replace_draft("<old@loom>", message)

    assert _writes(client) == [], "the fetched header is the ground truth, not the search"


def test_replace_draft_uid_expunge_targets_only_that_uid_and_other_deleted_mail_survives(monkeypatch, tmp_path):
    client = FakeIMAPClient(search_hits=b"7", capabilities=("UIDPLUS",))
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    transport.replace_draft("<old@loom>", message)

    expunges = [call for call in client.calls if call == ("uid", "EXPUNGE", "7")]
    assert len(expunges) == 1, "UID EXPUNGE must fire once, for the matched uid alone"
    assert not any(call[0] == "expunge" for call in client.calls), "bare EXPUNGE never runs"
    assert not any(call[:2] == ("uid", "EXPUNGE") and call[2] != "7" for call in client.calls)
    assert client.messages == {"9": r"(\Deleted \Seen)"}, "the old draft is gone, other deleted mail survives"


def test_replace_draft_without_uidplus_appends_and_keeps_old(monkeypatch, tmp_path):
    client = FakeIMAPClient(capabilities=())
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    result = transport.replace_draft("<old@loom>", message)

    assert "kept on the server" in result
    assert _message_id(message) in result
    assert not any(call[:2] == ("uid", "EXPUNGE") for call in client.calls)
    assert ("append", '"Drafts"', r"(\Draft)") in client.calls
    assert ("uid", "STORE", "7", "+FLAGS.SILENT", r"(\Deleted)") in client.calls, (
        "without UIDPLUS the old draft is still marked \\Deleted, waiting for the server to expunge it"
    )


def test_replace_draft_aborts_when_store_fails(monkeypatch, tmp_path):
    client = FakeIMAPClient(store_status="NO")
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    with pytest.raises(RuntimeError, match="two copies") as exc_info:
        transport.replace_draft("<old@loom>", message)

    assert "NO" in str(exc_info.value), "the error names the failed IMAP status"
    assert "appended" in str(exc_info.value), "the error says the replacement is already on the server"
    assert not any(call[:2] == ("uid", "EXPUNGE") for call in client.calls)


def test_replace_draft_with_zero_matches_raises_and_never_writes(monkeypatch, tmp_path):
    client = FakeIMAPClient(search_hits=b"")
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    with pytest.raises(ValueError, match="found 0"):
        transport.replace_draft("<missing@loom>", message)

    assert _writes(client) == []


def test_replace_draft_rejects_a_non_message_id_before_touching_imap(monkeypatch, tmp_path):
    client = FakeIMAPClient()
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    with pytest.raises(ValueError, match="not a Message-ID"):
        transport.replace_draft('x" OR SUBJECT "y', message)

    assert client.calls == []


def test_replace_draft_rejects_a_draft_id_over_the_rfc_line_bound(monkeypatch, tmp_path):
    client = FakeIMAPClient()
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    with pytest.raises(ValueError, match="998"):
        transport.replace_draft("<" + "a" * 997 + ">", message)

    assert client.calls == []


def test_replace_draft_accepts_a_draft_id_at_the_rfc_line_bound(monkeypatch, tmp_path):
    client = FakeIMAPClient(search_hits=b"")
    _patched_imap(monkeypatch, client)
    transport = ImapMailTransport(make_settings(tmp_path))
    message = build_draft_message("peer@example.com", "v2", "body", "owner@example.com")

    with pytest.raises(ValueError, match="found 0"):
        transport.replace_draft("<" + "a" * 996 + ">", message)


def test_mail_folder_inbox_is_rejected_by_config():
    with pytest.raises(ConfigError, match="LOOM_MAIL_FOLDER"):
        Settings(loom_env="dev", mail_folder="INBOX")
    with pytest.raises(ConfigError, match="LOOM_MAIL_FOLDER"):
        Settings(loom_env="dev", mail_folder="sent")
    assert Settings(loom_env="dev", mail_folder="[Gmail]/Drafts").mail_folder == "[Gmail]/Drafts"


class _LoginFailsClient:
    """login() raises like a real server rejecting bad credentials.

    The error echoes the password back, as some servers do, so the scrub
    in the error path is genuinely exercised.
    """

    def __init__(self):
        self.calls = []
        self.capabilities = ["UIDPLUS"]

    def login(self, user, password):
        self.calls.append(("login", user))
        raise imaplib.IMAP4.error(f"[AUTHENTICATIONFAILED] {password} rejected")

    def logout(self):
        self.calls.append(("logout",))
        return "OK", [b"Bye"]


def test_imap_errors_have_no_cause_chain(monkeypatch, tmp_path):
    client = _LoginFailsClient()
    monkeypatch.setattr("loom.mail.imaplib.IMAP4_SSL", lambda host, port: client)
    settings = make_settings(tmp_path, mail_password="secret-pass-42")
    transport = ImapMailTransport(settings)

    with pytest.raises(RuntimeError, match="LOOM_MAIL_USER") as exc_info:
        transport.append_draft(build_draft_message("a@b.com", "s", "b", "owner@example.com"))

    assert "***" in str(exc_info.value), "the scrub replaces the password the server echoed"
    assert "secret-pass-42" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None, "the chained exception can echo the password"
    assert exc_info.value.__suppress_context__ is True


class _LoginAndLogoutFailClient:
    """login() echoes the password; the server is already gone by logout()."""

    def __init__(self):
        self.calls = []
        self.capabilities = ["UIDPLUS"]

    def login(self, user, password):
        self.calls.append(("login", user))
        raise imaplib.IMAP4.error(f"[AUTHENTICATIONFAILED] {password} rejected")

    def logout(self):
        self.calls.append(("logout",))
        raise OSError("EOF read where a bytestring was expected")


def test_login_failure_survives_a_failing_logout_and_still_scrubs_the_password(monkeypatch, tmp_path):
    client = _LoginAndLogoutFailClient()
    monkeypatch.setattr("loom.mail.imaplib.IMAP4_SSL", lambda host, port: client)
    settings = make_settings(tmp_path, mail_password="secret-pass-42")
    transport = ImapMailTransport(settings)

    try:
        transport.append_draft(build_draft_message("a@b.com", "s", "b", "owner@example.com"))
    except RuntimeError as exc:
        assert "LOOM_MAIL_USER" in str(exc)
        assert "secret-pass-42" not in str(exc)
        assert exc.__cause__ is None, "the chained exception can echo the password"
        assert "secret-pass-42" not in traceback.format_exc()
    else:
        raise AssertionError("append_draft must raise the redacted RuntimeError, not the logout error")
