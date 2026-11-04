"""Task State: the persistent state machine for durable life work (Gate 6).

`TaskStore` keeps tasks, their transition history, and the idempotency ledger
for external side effects in `<data dir>/tasks.db`; `TaskStatus` and
`check_transition` are the machine every transition goes through.
"""

from loom.tasks.machine import TERMINAL_STATUSES, TaskStateError, TaskStatus, check_transition
from loom.tasks.store import Task, TaskEvent, TaskStore

__all__ = [
    "TERMINAL_STATUSES",
    "Task",
    "TaskEvent",
    "TaskStateError",
    "TaskStatus",
    "TaskStore",
    "check_transition",
]
