"""Run one routine through the shared graph, or resume a paused run after a human decision.

Both entry points are the only non-chat callers of `runtime.open_graph`; there is
no second orchestration path. Trigger and outcome land in the audit JSONL here;
approval decisions and tool executions are audited by the graph's own nodes.
"""

import logging
import uuid
from datetime import datetime

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from loom.audit import AuditLog
from loom.config import Settings
from loom.logging import set_run_id
from loom.pending import PendingApproval, PendingStore
from loom.routines import Routine, RoutineStore, now_local
from loom.runtime import open_graph

logger = logging.getLogger("loom")


def _settle(store: RoutineStore, routine_id: str, status: str) -> None:
    """Record the outcome on the routine; a routine removed mid-pending has no row left."""
    try:
        store.set_status(routine_id, now_local(), status)
    except KeyError:
        logger.warning("routine row gone, status not settled", extra={"routine_id": routine_id, "last_status": status})

logger = logging.getLogger("loom")


def _thread_id(routine_id: str) -> str:
    return f"routine:{routine_id}:{int(datetime.now().timestamp() * 1000)}"


def run_routine(
    routine: Routine,
    settings: Settings,
    store: RoutineStore,
    pending: PendingStore,
    graph=None,
    *,
    details: dict | None = None,
) -> str:
    """Trigger one routine run; returns "completed" or "pending".

    A run that reaches the approval gate persists a PendingApproval and stops
    without resuming. `details`, when given, receives thread_id plus pending_id
    (pending path) or the final answer (completed path) for CLI and demo output.
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

    def drive(opened) -> str:
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
                status="pending",
            )
            pending.add(record)
            _settle(store, routine.id, "pending")
            logger.info(
                "routine run waits for approval",
                extra={"routine_id": routine.id, "thread_id": thread_id, "pending_id": record.pending_id},
            )
            if details is not None:
                details.update({"thread_id": thread_id, "pending_id": record.pending_id})
            return "pending"
        _settle(store, routine.id, "completed")
        answer = result["messages"][-1].content
        logger.info(
            "routine run completed",
            extra={"routine_id": routine.id, "thread_id": thread_id, "answer": answer},
        )
        if details is not None:
            details.update({"thread_id": thread_id, "answer": answer})
        return "completed"

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
    if record.status != "pending":
        raise ValueError(f"pending approval {pending_id} is already {record.status}")
    store = RoutineStore(settings.loom_data_dir)
    config = {"configurable": {"thread_id": record.thread_id}}

    def drive(opened) -> str:
        result = opened.invoke(Command(resume={"decision": decision}), config)
        if "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            pending.set_status(pending_id, "pending", tools=payload["tools"])
            logger.info(
                "resumed routine run waits for approval again",
                extra={"thread_id": record.thread_id, "pending_id": pending_id},
            )
            return "pending"
        pending.set_status(pending_id, "completed")
        _settle(store, record.routine_id, "completed")
        logger.info(
            "pending approval resolved",
            extra={"thread_id": record.thread_id, "pending_id": pending_id, "decision": decision},
        )
        return result["messages"][-1].content

    if graph is None:
        with open_graph(settings) as opened:
            return drive(opened)
    return drive(graph)
