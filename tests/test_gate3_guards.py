"""Gate 3 guards: the registry has no send path and no calendar write path.

These are the plan's gate tests: exact side-effect flags for every tool, a
source-level ban on send and calendar-write symbols, an IMAP verb allowlist,
and proof that credentials never travel through tool arguments, messages, or
audit-rendered args.
"""

import re
from pathlib import Path

import pytest
from conftest import FakeMailTransport

from loom.audit import summarize_args
from loom.config import Settings
from loom.logging import is_sensitive
from loom.tools import make_tools

_SRC = Path(__file__).resolve().parents[1] / "src" / "loom"

# Symbols the plan forbids anywhere in src/loom. If one appears, this file goes red.
FORBIDDEN_SYMBOLS = (
    "send_email", "sendmail", "smtplib", "send_message", "SMTP",
    "events.insert", "events.patch", "events.update", "events.delete",
)

# The only IMAP verbs the mail module may use: read the mailbox and manage
# drafts by UID. Bare store/search/expunge are gone; UID-only is the rule.
IMAP_VERBS = {"uid", "append", "select", "login", "logout"}


def source_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(_SRC.glob("*.py")))


def make_registry(tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    return make_tools(settings)


def test_registry_side_effect_flags_are_exact(tmp_path):
    registry = make_registry(tmp_path)

    assert set(registry) == {
        "get_time", "echo", "list_dir", "read_file", "write_file", "mkdir", "move_file", "delete_file",
        "propose_sandbox_note", "calendar_list_events", "calendar_summarize_range",
        "mail_create_draft", "mail_update_draft",
    }
    assert {name for name, spec in registry.items() if spec.side_effect} == {
        "write_file", "mkdir", "move_file", "delete_file", "propose_sandbox_note",
        "mail_create_draft", "mail_update_draft",
    }
    assert {name for name, spec in registry.items() if not spec.side_effect} == {
        "get_time", "echo", "list_dir", "read_file",
        "calendar_list_events", "calendar_summarize_range",
    }


@pytest.mark.parametrize("symbol", FORBIDDEN_SYMBOLS)
def test_forbidden_symbols_never_appear_in_source(symbol):
    assert symbol not in source_text(), f"forbidden symbol in src/loom: {symbol}"


def test_mail_module_uses_only_draft_and_read_imap_verbs():
    text = (_SRC / "mail.py").read_text(encoding="utf-8")

    verbs = set(re.findall(r"client\.(\w+)\(", text))
    module_uses = set(re.findall(r"imaplib\.(IMAP4_SSL|IMAP4\.error)", text))

    assert verbs <= IMAP_VERBS, verbs
    assert module_uses == {"IMAP4_SSL", "IMAP4.error"}, module_uses


def test_mail_tool_args_and_results_never_carry_the_password(tmp_path):
    password = "app-password-x9-very-secret"
    settings = Settings(
        loom_env="dev",
        loom_data_dir=tmp_path,
        loom_sandbox_root=tmp_path / "sandbox",
        imap_host="imap.example.com",
        mail_user="owner@example.com",
        mail_password=password,
    )
    transport = FakeMailTransport()
    registry = make_tools(settings, {"mail": transport})

    created = registry["mail_create_draft"].func.invoke(
        {"to": "peer@example.com", "subject": "hi", "body": "the plan"}
    )
    updated = registry["mail_update_draft"].func.invoke(
        {"draft_id": "fake-draft-1", "to": "peer@example.com", "subject": "hi", "body": "the plan"}
    )

    stored = [message.decode("utf-8", "replace") for message in transport.appended]
    stored += [message.decode("utf-8", "replace") for _, message in transport.replaced]
    assert stored, "the fake transport must see the drafts for this check to mean anything"
    for text in [*stored, created, updated]:
        assert password not in text


def test_credential_names_are_masked_by_the_single_redaction_home():
    assert is_sensitive("mail_password")
    assert is_sensitive("LOOM_MAIL_PASSWORD")
    assert summarize_args({"mail_password": "leak-me", "to": "peer@example.com"}) == (
        '{"mail_password": "***", "to": "peer@example.com"}'
    )
