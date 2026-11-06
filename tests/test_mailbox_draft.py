"""The layer-4 mailbox-draft control plane: separate scope, gateway first, exactly one append."""

import json

import pytest
from conftest import FakeMailTransport, read_audit

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.gateway import ScopeStore
from loom.gateway.mailbox_draft import MAILBOX_DRAFT_ROUTINE, request_mailbox_draft, resolve_mailbox_draft
from loom.gateway.pending import PendingStore
from loom.tools.drafts import DraftStore


def make_settings(tmp_path, imap_host="imap.example.com"):
    return Settings(
        loom_env="dev",
        loom_data_dir=tmp_path,
        loom_sandbox_root=tmp_path / "sandbox",
        imap_host=imap_host,
        mail_user="owner@example.com",
    )


def make_request(tmp_path, settings, category="reply"):
    draft = DraftStore(tmp_path).add(
        to="landlord@example.com", subject="Heating bill", body="We agree and will pay by the 15th.", category=category
    )
    seams = {
        "pending": PendingStore(tmp_path),
        "scopes": ScopeStore(tmp_path),
        "audit": AuditLog(tmp_path),
    }
    record = request_mailbox_draft(
        draft.draft_id, settings, seams["pending"], seams["scopes"], seams["audit"]
    )
    return draft, record, seams


def test_request_parks_a_pending_with_full_args_under_its_own_scope(tmp_path):
    settings = make_settings(tmp_path)
    draft, record, seams = make_request(tmp_path, settings)

    assert record.routine_id == MAILBOX_DRAFT_ROUTINE
    assert record.thread_id == f"mailbox:{draft.draft_id}"
    assert record.status == "pending" and record.task_id == ""
    assert record.tools == [
        {"name": "mail_create_draft", "args": {"to": draft.to, "subject": draft.subject, "body": draft.body}}
    ]
    scope = seams["scopes"].get(record.scope_id)
    assert scope.allowed_actions == {"mail_create_draft", "mail_update_draft"}
    assert scope.memory_write is False and scope.approval_required is True
    assert scope.send_permission is False
    row = read_audit(tmp_path)[-1]
    assert row["actor"] == "console" and row["tool"] == "mailbox_draft.request" and row["decision"] == "requested"
    assert row["correlation_id"] == scope.correlation_id


def test_request_refuses_when_mail_is_not_configured(tmp_path):
    settings = make_settings(tmp_path, imap_host="")

    with pytest.raises(RuntimeError, match="mail is not configured"):
        request_mailbox_draft(
            "whatever", settings, PendingStore(tmp_path), ScopeStore(tmp_path), AuditLog(tmp_path)
        )
    assert PendingStore(tmp_path).list_pending() == []


def test_resolve_approve_appends_exactly_once_with_the_full_audit_chain(tmp_path):
    settings = make_settings(tmp_path)
    _draft, record, seams = make_request(tmp_path, settings)
    transport = FakeMailTransport()

    answer = resolve_mailbox_draft(record.pending_id, "approve", settings, seams["pending"], transport=transport)

    assert "has not been sent" in answer
    assert len(transport.appended) == 1
    message = transport.appended[0].decode("utf-8", "replace")
    assert "landlord@example.com" in message and "Heating bill" in message
    assert "We agree and will pay by the 15th." in message
    assert seams["pending"].get(record.pending_id).status == "completed"

    correlation = seams["scopes"].get(record.scope_id).correlation_id
    chain = [(row["actor"], row["decision"]) for row in read_audit(tmp_path) if row.get("correlation_id") == correlation]
    assert chain.count(("gateway", "authorized")) == 1  # the ledger write that gated the append
    assert ("human", "approved") in chain and ("loom", "executed") in chain

    with pytest.raises(ValueError, match="already"):
        resolve_mailbox_draft(record.pending_id, "approve", settings, seams["pending"], transport=transport)
    assert len(transport.appended) == 1  # the status guard is the re-execution lock


def test_resolve_reject_appends_nothing(tmp_path):
    settings = make_settings(tmp_path)
    _draft, record, seams = make_request(tmp_path, settings)
    transport = FakeMailTransport()

    answer = resolve_mailbox_draft(record.pending_id, "reject", settings, seams["pending"], transport=transport)

    assert answer == "the mailbox draft was rejected; nothing was written"
    assert transport.appended == []
    assert seams["pending"].get(record.pending_id).status == "completed"
    correlation = seams["scopes"].get(record.scope_id).correlation_id
    chain = [(row["actor"], row["decision"]) for row in read_audit(tmp_path) if row.get("correlation_id") == correlation]
    assert ("human", "rejected") in chain and ("loom", "executed") not in chain


def test_resolve_with_a_broken_audit_fails_closed_and_touches_no_imap(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    _draft, record, seams = make_request(tmp_path, settings)
    transport = FakeMailTransport()

    def broken_append(**kwargs):
        raise OSError("audit disk full")

    monkeypatch.setattr(AuditLog, "append", broken_append)
    answer = resolve_mailbox_draft(record.pending_id, "approve", settings, seams["pending"], transport=transport)

    assert "refused" in answer and "failing closed" in answer
    assert transport.appended == []
    assert seams["pending"].get(record.pending_id).status == "pending"  # still waiting: nothing resolved


def test_resolve_approve_without_a_transport_audits_the_failure_and_closes(tmp_path):
    settings = make_settings(tmp_path)
    _draft, record, seams = make_request(tmp_path, settings)

    answer = resolve_mailbox_draft(record.pending_id, "approve", settings, seams["pending"], transport=None)

    assert "could not be written" in answer and "nothing was sent" in answer
    assert seams["pending"].get(record.pending_id).status == "completed"
    correlation = seams["scopes"].get(record.scope_id).correlation_id
    chain = [(row["actor"], row["decision"]) for row in read_audit(tmp_path) if row.get("correlation_id") == correlation]
    assert ("human", "approved") in chain and ("loom", "failed") in chain


def test_resolve_routes_the_args_and_marks_a_failed_append_completed(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    _draft, record, seams = make_request(tmp_path, settings)

    class ExplodingTransport:
        def append_draft(self, message_bytes):
            raise RuntimeError("IMAP append draft failed (NO)")

    answer = resolve_mailbox_draft(record.pending_id, "approve", settings, seams["pending"], transport=ExplodingTransport())

    assert "could not be written" in answer
    assert seams["pending"].get(record.pending_id).status == "completed"  # cannot be re-executed
    correlation = seams["scopes"].get(record.scope_id).correlation_id
    failed = [row for row in read_audit(tmp_path) if row.get("correlation_id") == correlation and row["decision"] == "failed"]
    assert failed and "RuntimeError" in failed[0]["args"]
    args = json.loads(failed[0]["args"]) if isinstance(failed[0]["args"], str) else failed[0]["args"]
    assert args["draft_id"] == record.thread_id.removeprefix("mailbox:")


def test_a_gateway_veto_closes_the_record_and_never_touches_imap(tmp_path):
    """P1-6: an expired scope must not leave an unclearable pending; reject and approve both close."""
    settings = make_settings(tmp_path)
    _draft, record, seams = make_request(tmp_path, settings)
    transport = FakeMailTransport()

    # Age the layer-4 scope past its expiry the way a long-idle queue looks.
    raw = json.loads((tmp_path / "scopes.json").read_text(encoding="utf-8"))
    raw[0]["expires_at"] = "2020-01-01T00:00:00+08:00"
    (tmp_path / "scopes.json").write_text(json.dumps(raw), encoding="utf-8")

    answer = resolve_mailbox_draft(record.pending_id, "reject", settings, seams["pending"], transport=transport)

    assert "refused" in answer and "closed" in answer
    assert transport.appended == []  # no IMAP call on either path
    assert seams["pending"].get(record.pending_id).status != "pending"
    rows = read_audit(tmp_path)
    vetoes = [row for row in rows if row["decision"] == "rejected" and row["tool"] == "mail_create_draft"]
    assert vetoes and "expired" in json.loads(vetoes[0]["args"])["reason"]


def test_a_gateway_veto_on_approve_also_closes_without_appending(tmp_path):
    settings = make_settings(tmp_path)
    _draft, record, seams = make_request(tmp_path, settings)
    transport = FakeMailTransport()
    raw = json.loads((tmp_path / "scopes.json").read_text(encoding="utf-8"))
    raw[0]["expires_at"] = "2020-01-01T00:00:00+08:00"
    (tmp_path / "scopes.json").write_text(json.dumps(raw), encoding="utf-8")

    resolve_mailbox_draft(record.pending_id, "approve", settings, seams["pending"], transport=transport)

    assert transport.appended == []
    assert seams["pending"].get(record.pending_id).status == "completed"
