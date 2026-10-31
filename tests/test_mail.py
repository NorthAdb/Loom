"""Mail module: message building, the tool surface, and IMAP error paths."""

from email import message_from_bytes, policy

import pytest
from conftest import FakeMailTransport

from loom.config import Settings
from loom.mail import ImapMailTransport, build_draft_message
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
