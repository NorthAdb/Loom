"""Run one routine through the shared graph, or resume a paused run after a human decision.

Both entry points are the only non-chat callers of `runtime.open_graph`; there is
no second orchestration path. The trigger lands in the audit JSONL here; approval
decisions and tool executions are audited by the graph's own nodes.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from loom.audit import AuditLog
from loom.config import Settings
from loom.logging import set_run_id
from loom.pending import COMPLETED, PENDING, PendingApproval, PendingStore
from loom.routines import Routine, RoutineStore, now_local
from loom.runtime import open_graph

logger = logging.getLogger("loom")


@dataclass(frozen=True)
class RunOutcome:
    """What one routine run or resume did; the CLI and demo print from this."""

    outcome: str  # PENDING or COMPLETED
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
    """Trigger one routine run; pending when the gate holds the run.

    A run that reaches the approval gate persists a PendingApproval and stops
    without resuming.
    """
    if not routine.enabled:
        raise ValueError(f"routine is disabled: {routine.id}")
    audit = AuditLog(settings.loom_data_dir)
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
    config = {"configurable": {"thread_id": thread_id}}

    def drive(opened) -> RunOutcome:
        result = opened.invoke({"messages": [HumanMessage(content=routine.prompt)], "run_id": run_id}, config)
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
