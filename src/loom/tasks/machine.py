"""The task state machine: the transitions a life task may take, in one place.

A task is durable work the operator tracks across restarts. The machine is the
single authority on which status may follow which, so a caller cannot mark a
task completed while it still waits for a human, and nothing leaves a terminal
state. Stores and callers ask `check_transition` before they move a task.
"""

from enum import Enum


class TaskStatus(str, Enum):
    """The statuses a task moves through; the last three are terminal."""

    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

# Everything not listed here is illegal. waiting_approval can only return to
# running (a human approved) or leave the machine (rejected or the run failed);
# it can never jump straight to completed, because the side effect has not run.
_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.BLOCKED,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING_APPROVAL: frozenset(
        {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.BLOCKED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


class TaskStateError(ValueError):
    """Raised when a transition is not part of the machine."""


def check_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Raise TaskStateError unless current -> target is a machine transition."""
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise TaskStateError(f"illegal task transition {current.value} -> {target.value}")
