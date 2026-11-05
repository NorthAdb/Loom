"""Run one routine or one triage through the shared graph, or resume a paused run after a decision.

These entry points are the only non-chat callers of `runtime.open_graph`; there is
no second orchestration path. The trigger lands in the audit JSONL here; approval
decisions and tool executions are audited by the graph's own nodes.
`resolve_pending` is the single decision entry the CLI and the console call:
graph-backed records resume there, mailbox-draft records resolve through their
own deterministic control plane.
"""

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.core.logging import set_run_id
from loom.core.runtime import open_graph
from loom.gateway.mailbox_draft import MAILBOX_DRAFT_ROUTINE, resolve_mailbox_draft
from loom.gateway.pending import COMPLETED, PENDING, PendingApproval, PendingStore
from loom.gateway.scope import ScopeStore
from loom.inbound.accept import compose_triage_prompt
from loom.inbound.store import InboxStore
from loom.ops.budget import guard_from_settings, turn_tokens
from loom.ops.routines import Routine, RoutineStore, now_local
from loom.tasks import TaskStatus, TaskStore

logger = logging.getLogger("loom")

TRIAGE_ROUTINE = "inbound_triage"  # the routine_id a triage pending carries; matches accept_inbound's task kind


@dataclass(frozen=True)
class RunOutcome:
    """What one routine run or resume did; the CLI and demo print from this."""

    outcome: Literal["pending", "completed", "refused"]
    thread_id: str
    pending_id: str = ""
    answer: str = ""


def _settle(store: RoutineStore, routine_id: str, status: str) -> None:
    """Record the outcome on the routine; a routine removed mid-pending has no row left."""
    try:
        store.set_status(routine_id, now_local(), status)
    except KeyError:
        logger.warning("routine row gone, status not settled", extra={"routine_id": routine_id, "last_status": status})


def _thread_id(routine_id: str) -> str:
    return f"routine:{routine_id}:{int(datetime.now().timestamp() * 1000)}"


def run_routine(
    routine: Routine,
    settings: Settings,
    store: RoutineStore,
    pending: PendingStore,
    graph=None,
) -> RunOutcome:
    """Trigger one routine run; pending when the gate holds the run, refused when the daily token budget is spent.

    A run that reaches the approval gate persists a PendingApproval and stops
    without resuming.
    """
    if not routine.enabled:
        raise ValueError(f"routine is disabled: {routine.id}")
    audit = AuditLog(settings.loom_data_dir)
    guard = guard_from_settings(settings, settings.loom_data_dir)
    thread_id = _thread_id(routine.id)
    run_id = uuid.uuid4().hex
    set_run_id(run_id)
    audit.append(
        run_id=run_id,
        actor="routine",
        tool="routine_trigger",
        args={"routine_id": routine.id, "thread_id": thread_id},
        decision="triggered",
    )
    if guard is not None and not guard.allow("routine", run_id):
        logger.warning(
            "routine run refused by the daily token budget",
            extra={"routine_id": routine.id, "refusal": guard.refusal_line()},
        )
        return RunOutcome("refused", thread_id, answer=guard.refusal_line())
    config = {"configurable": {"thread_id": thread_id}}

    def drive(opened) -> RunOutcome:
        result = opened.invoke({"messages": [HumanMessage(content=routine.prompt)], "run_id": run_id}, config)
        if guard is not None:
            guard.record("routine", run_id, turn_tokens(result["messages"]))
        if "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            record = PendingApproval(
                pending_id=uuid.uuid4().hex,
                thread_id=thread_id,
                routine_id=routine.id,
                tools=payload["tools"],
                reason=payload["reason"],
                created_at=now_local(),
                status=PENDING,
            )
            pending.add(record)
            _settle(store, routine.id, PENDING)
            logger.info(
                "routine run waits for approval",
                extra={"routine_id": routine.id, "thread_id": thread_id, "pending_id": record.pending_id},
            )
            return RunOutcome(PENDING, thread_id, pending_id=record.pending_id)
        _settle(store, routine.id, COMPLETED)
        answer = result["messages"][-1].content
        logger.info(
            "routine run completed",
            extra={"routine_id": routine.id, "thread_id": thread_id, "answer": answer},
        )
        return RunOutcome(COMPLETED, thread_id, answer=answer)

    if graph is None:
        with open_graph(settings) as opened:
            return drive(opened)
    return drive(graph)


def resume_pending(pending_id: str, decision: str, settings: Settings, pending: PendingStore, graph=None) -> str:
    """Apply a human decision to one pending record; returns the final answer or "pending".

    A resumed run can hit the gate again for further side-effect calls; the same
    record stays pending with refreshed tools and the marker "pending" is
    returned. No audit here: the gate node audits the decision, run_tools audits
    execution.
    """
    if decision not in ("approve", "reject"):
        raise ValueError(f"decision must be 'approve' or 'reject', got {decision!r}")
    record = pending.get(pending_id)
    if record.status != PENDING:
        raise ValueError(f"pending approval {pending_id} is already {record.status}")
    store = RoutineStore(settings.loom_data_dir)
    config = {"configurable": {"thread_id": record.thread_id}}

    def drive(opened) -> RunOutcome:
        result = opened.invoke(Command(resume={"decision": decision}), config)
        if "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            pending.set_status(pending_id, PENDING, tools=payload["tools"])
            logger.info(
                "resumed routine run waits for approval again",
                extra={"thread_id": record.thread_id, "pending_id": pending_id},
            )
            return RunOutcome(PENDING, record.thread_id, pending_id=pending_id)
        pending.set_status(pending_id, COMPLETED)
        _settle(store, record.routine_id, COMPLETED)
        logger.info(
            "pending approval resolved",
            extra={"thread_id": record.thread_id, "pending_id": pending_id, "decision": decision},
        )
        return RunOutcome(COMPLETED, record.thread_id, answer=result["messages"][-1].content)

    if graph is None:
        with open_graph(settings) as opened:
            return drive(opened)
    return drive(graph)


def _settle_task(store: TaskStore, task_id: str, status: TaskStatus, *, reason: str | None = None, result: str | None = None) -> None:
    """Move the triage task with its run; bookkeeping that never breaks the run.

    The row can be gone or already terminal when a message is triaged twice;
    the audit trail is the record then, so store and machine errors are
    logged and swallowed.
    """
    try:
        store.transition(task_id, status, reason=reason, result=result)
    except (KeyError, ValueError, sqlite3.Error) as exc:
        logger.warning("task bookkeeping skipped", extra={"task_id": task_id, "target": status.value, "error": str(exc)})


def run_triage(message_id: str, settings: Settings, pending: PendingStore, *, graph=None) -> RunOutcome:
    """Trigger one triage turn for an accepted inbox message.

    The turn runs like a routine run — budget-guarded (kind "triage"),
    threaded as triage:{task_id}, triggered in the audit — but scoped: the
    graph is invoked with the scope the accept step issued, so the gateway
    checks every proposal against it. A message with no scope (an
    unauthenticated sender) is skipped without a graph run: an
    unauthenticated turn must not run with tool rights at all.
    """
    audit = AuditLog(settings.loom_data_dir)
    try:
        record = InboxStore(settings.loom_data_dir).get(message_id)
    except KeyError as exc:
        raise ValueError(exc.args[0]) from None
    run_id = uuid.uuid4().hex
    set_run_id(run_id)
    thread_id = f"triage:{record.task_id}"
    correlation_id = None
    if record.scope_id:
        try:
            correlation_id = ScopeStore(settings.loom_data_dir).get(record.scope_id).correlation_id
        except KeyError:
            correlation_id = None  # scopes are excluded from backups; a dangling id is the gateway's refusal
    audit.append(
        run_id=run_id,
        actor="console",
        tool="inbound.triage",
        args={"message_id": message_id, "task_id": record.task_id},
        decision="triggered",
        correlation_id=correlation_id,
    )
    if not record.scope_id:
        audit.append(
            run_id=run_id,
            actor="console",
            tool="inbound.triage",
            args={"message_id": message_id},
            decision="skipped",
        )
        return RunOutcome(COMPLETED, thread_id, answer="no scope was issued for this message; nothing to triage")
    guard = guard_from_settings(settings, settings.loom_data_dir)
    if guard is not None and not guard.allow("triage", run_id):
        logger.warning("triage run refused by the daily token budget", extra={"message_id": message_id, "refusal": guard.refusal_line()})
        return RunOutcome("refused", thread_id, answer=guard.refusal_line())
    config = {"configurable": {"thread_id": thread_id}}
    tasks = TaskStore(settings.loom_data_dir)

    def drive(opened) -> RunOutcome:
        result = opened.invoke(
            {
                "messages": [HumanMessage(content=compose_triage_prompt(record.message))],
                "run_id": run_id,
                "scope_id": record.scope_id or "",
            },
            config,
        )
        if guard is not None:
            guard.record("triage", run_id, turn_tokens(result["messages"]))
        if "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            triage_pending = PendingApproval(
                pending_id=uuid.uuid4().hex,
                thread_id=thread_id,
                routine_id=TRIAGE_ROUTINE,
                tools=payload["tools"],
                reason=payload["reason"],
                created_at=now_local(),
                status=PENDING,
                scope_id=record.scope_id or "",
                task_id=record.task_id,
            )
            pending.add(triage_pending)
            _settle_task(tasks, record.task_id, TaskStatus.WAITING_APPROVAL, reason="triage waits for the draft approval")
            logger.info(
                "triage run waits for approval",
                extra={"message_id": message_id, "thread_id": thread_id, "pending_id": triage_pending.pending_id},
            )
            return RunOutcome(PENDING, thread_id, pending_id=triage_pending.pending_id)
        answer = result["messages"][-1].content
        _settle_task(tasks, record.task_id, TaskStatus.COMPLETED, result=str(answer))
        logger.info("triage run completed", extra={"message_id": message_id, "thread_id": thread_id})
        return RunOutcome(COMPLETED, thread_id, answer=answer)

    if graph is None:
        with open_graph(settings) as opened:
            return drive(opened)
    return drive(graph)


def resolve_pending(
    pending_id: str,
    decision: str,
    settings: Settings,
    pending: PendingStore,
    *,
    graph=None,
    mail_transport=None,
) -> RunOutcome:
    """Apply a human decision through the right path; the single entry the CLI and console call.

    A mailbox_draft record resolves deterministically through its control
    plane (no model, always audited); anything else resumes the paused graph
    run. When a triage run completes — approve or reject — its task settles
    through the state machine.
    """
    record = pending.get(pending_id)
    if record.routine_id == MAILBOX_DRAFT_ROUTINE:
        answer = resolve_mailbox_draft(pending_id, decision, settings, pending, transport=mail_transport)
        return RunOutcome(COMPLETED, record.thread_id, answer=answer)
    outcome = resume_pending(pending_id, decision, settings, pending, graph=graph)
    if outcome.outcome == COMPLETED and record.routine_id == TRIAGE_ROUTINE and record.task_id:
        tasks = TaskStore(settings.loom_data_dir)
        try:
            current = tasks.get(record.task_id).status
        except (KeyError, sqlite3.Error):
            return outcome
        if current is TaskStatus.WAITING_APPROVAL:
            # The machine forbids waiting_approval -> completed: the human has
            # decided, so the run resumes to running before it completes.
            _settle_task(tasks, record.task_id, TaskStatus.RUNNING, reason="human decision received")
        _settle_task(tasks, record.task_id, TaskStatus.COMPLETED, result=outcome.answer)
    return outcome
