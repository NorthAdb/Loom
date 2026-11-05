"""Layer 4: the local draft -> IMAP mailbox draft control plane (no model).

`request_mailbox_draft` mints a scope of its own for the Gate 3 mail tools
and parks the request in the pending queue; `resolve_mailbox_draft` applies
the human decision through the Tool Gateway, audit first — the gateway's
authorization row is part of its check, so an audit write failure fails
closed and no IMAP call happens. The approved draft is APPENDed to the
drafts folder with the \\Draft flag; it is never sent. This scope is not the
triage scope: layer 4 is a separate authorization and a separate approval,
and the default triage pipeline never enters here.
"""

import uuid
from typing import Protocol

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.gateway.pending import COMPLETED, PENDING, PendingApproval, PendingStore
from loom.gateway.scope import ScopeStore, issue_scope
from loom.gateway.tool_gateway import ToolGateway
from loom.ops.routines import now_local
from loom.tools.drafts import DraftStore
from loom.tools.mail import _scrub as _scrub_error
from loom.tools.mail import build_draft_message

MAILBOX_DRAFT_ROUTINE = "mailbox_draft"
_ACTIONS = frozenset({"mail_create_draft", "mail_update_draft"})


class MailboxTransport(Protocol):
    """What resolve needs to reach the drafts folder; a test double records instead."""

    def append_draft(self, message_bytes: bytes) -> str: ...


def request_mailbox_draft(
    draft_id: str,
    settings: Settings,
    pending: PendingStore,
    scopes: ScopeStore,
    audit: AuditLog,
    *,
    transport: MailboxTransport | None = None,
) -> PendingApproval:
    """Park a 'write this local draft to the mailbox' request for human approval.

    Nothing touches IMAP here: the transport argument is accepted and unused,
    so a caller cannot accidentally execute at request time (fail closed) —
    the real transport is built at resolve time. Mail that is not configured
    refuses before any record exists. The request carries the draft's full
    to/subject/body in the pending payload, under a scope minted for this one
    request.
    """
    if transport is None and not settings.imap_host:
        raise RuntimeError("mail is not configured")
    draft = DraftStore(settings.loom_data_dir).get(draft_id)
    scope = issue_scope(
        "owner", "console", f"draft/{draft_id}", _ACTIONS, memory_write=False, approval_required=True
    )
    scopes.add(scope)
    record = PendingApproval(
        pending_id=uuid.uuid4().hex,
        thread_id=f"mailbox:{draft_id}",
        routine_id=MAILBOX_DRAFT_ROUTINE,
        tools=[{"name": "mail_create_draft", "args": {"to": draft.to, "subject": draft.subject, "body": draft.body}}],
        reason="write this local draft to the mailbox drafts folder over IMAP APPEND (never sent)",
        created_at=now_local(),
        status=PENDING,
        scope_id=scope.scope_id,
        task_id="",
    )
    audit.append(
        run_id=record.pending_id,
        actor="console",
        tool="mailbox_draft.request",
        args={"draft_id": draft_id},
        decision="requested",
        correlation_id=scope.correlation_id,
    )
    pending.add(record)
    return record


def resolve_mailbox_draft(
    pending_id: str,
    decision: str,
    settings: Settings,
    pending: PendingStore,
    *,
    transport: MailboxTransport | None,
) -> str:
    """Apply the human decision to one mailbox-draft request; returns the final answer.

    Every resolution is deterministic and audited: the gateway check runs
    before anything else and its authorization ledger write is part of the
    check, so an audit failure refuses with no IMAP call. A rejected or
    failed request is marked completed — the pending status guard is the
    re-execution lock, and the audit row is the record of what happened.
    """
    if decision not in ("approve", "reject"):
        raise ValueError(f"decision must be 'approve' or 'reject', got {decision!r}")
    record = pending.get(pending_id)
    if record.status != PENDING:
        raise ValueError(f"pending approval {pending_id} is already {record.status}")
    audit = AuditLog(settings.loom_data_dir)
    gateway = ToolGateway(audit, ScopeStore(settings.loom_data_dir))
    verdict = gateway.check(record.scope_id, "mail_create_draft", side_effect=True, run_id=pending_id)
    correlation_id = verdict.scope.correlation_id if verdict.scope else None
    tool_args = record.tools[0]["args"] if record.tools else {}
    draft_id = record.thread_id.removeprefix("mailbox:")
    if not verdict.allowed:
        return f"refused: {verdict.reason}"
    if decision == "reject":
        audit.append(
            run_id=pending_id,
            actor="human",
            tool="mail_create_draft",
            args=tool_args,
            decision="rejected",
            correlation_id=correlation_id,
        )
        pending.set_status(pending_id, COMPLETED)
        return "the mailbox draft was rejected; nothing was written"
    audit.append(
        run_id=pending_id,
        actor="human",
        tool="mail_create_draft",
        args=tool_args,
        decision="approved",
        correlation_id=correlation_id,
    )
    if transport is None:
        return _fail(audit, pending_id, draft_id, correlation_id, pending, "mail is not configured at resolve time")
    try:
        message = build_draft_message(
            str(tool_args.get("to", "")),
            str(tool_args.get("subject", "")),
            str(tool_args.get("body", "")),
            from_addr=settings.mail_user,
        )
        result_id = transport.append_draft(message)
    except Exception as exc:  # noqa: BLE001 - the failure is audited and the record closed, never re-raised
        return _fail(
            audit, pending_id, draft_id, correlation_id, pending, _scrub_error(exc, settings.mail_password)
        )
    audit.append(
        run_id=pending_id,
        actor="loom",
        tool="mail_create_draft",
        args={"draft_id": draft_id, "mailbox_result": result_id},
        decision="executed",
        correlation_id=correlation_id,
    )
    pending.set_status(pending_id, COMPLETED)
    return "draft written to the mailbox drafts folder; it has not been sent"


def _fail(
    audit: AuditLog, pending_id: str, draft_id: str, correlation_id: str | None, pending: PendingStore, error: str
) -> str:
    """Close a failed approval: the audit row is the record, the record cannot re-execute."""
    audit.append(
        run_id=pending_id,
        actor="loom",
        tool="mail_create_draft",
        args={"draft_id": draft_id, "error": error},
        decision="failed",
        correlation_id=correlation_id,
    )
    pending.set_status(pending_id, COMPLETED)
    return "the mailbox draft could not be written; the failure is in the audit log and nothing was sent"
