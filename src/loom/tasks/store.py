"""The persistent task store: tasks, their transition history, and effect receipts.

One sqlite db at `<data dir>/tasks.db` holds three tables. `tasks` is the
current state of every task; `task_events` is the append-only transition
history; `task_effects` is the idempotency ledger keyed by
(task_id, effect_key). Every status change goes through `transition`, which
asks the machine first, so no caller can invent a status. Reopening the store
after a process restart sees the same rows, which is what makes a task
survive a restart; `recover` then accounts for the runs that were mid-flight.
"""

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loom.memory.facts import contains_secret
from loom.ops.routines import now_local
from loom.tasks.machine import TERMINAL_STATUSES, TaskStateError, TaskStatus, check_transition

_TITLE_CAP = 500
_TEXT_CAP = 2000

_TASKS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS tasks ("
    "task_id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, "
    "idempotency_key TEXT UNIQUE, waiting_reason TEXT, error TEXT, result TEXT, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
)
_EVENTS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS task_events ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, from_status TEXT NOT NULL, "
    "to_status TEXT NOT NULL, reason TEXT, ts TEXT NOT NULL)"
)
_EFFECTS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS task_effects ("
    "task_id TEXT NOT NULL, effect_key TEXT NOT NULL, outcome TEXT, ts TEXT NOT NULL, "
    "PRIMARY KEY (task_id, effect_key))"
)


@dataclass(frozen=True)
class Task:
    """One durable unit of life work and the status it is in right now."""

    task_id: str
    kind: str
    title: str
    status: TaskStatus
    idempotency_key: str | None
    waiting_reason: str | None
    error: str | None
    result: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskEvent:
    """One row of the append-only transition history."""

    seq: int
    task_id: str
    from_status: str
    to_status: str
    reason: str | None
    ts: str


def _capped(text: str | None, cap: int) -> str | None:
    """The text, truncated to the cap; stored records stay bounded like audit values."""
    if text is None:
        return None
    return text if len(text) <= cap else text[:cap]


def _row_to_task(row: tuple) -> Task:
    return Task(
        task_id=row[0],
        kind=row[1],
        title=row[2],
        status=TaskStatus(row[3]),
        idempotency_key=row[4],
        waiting_reason=row[5],
        error=row[6],
        result=row[7],
        created_at=row[8],
        updated_at=row[9],
    )


class TaskStore:
    """CRUD and state-machine transitions over tasks.db.

    Writes are serialized per process like the other stores; run one worker
    per data dir (see README).
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "tasks.db"

    @contextmanager
    def _connection(self):
        """A tasks-db connection that commits on success and always closes.

        Same shape as the facts store: sqlite3's own `with` only manages the
        transaction, and an unclosed connection holds the file on Windows.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        try:
            conn.execute(_TASKS_SCHEMA)
            conn.execute(_EVENTS_SCHEMA)
            conn.execute(_EFFECTS_SCHEMA)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create(self, kind: str, title: str, *, idempotency_key: str | None = None) -> Task:
        """Register a new task in the created state and record its first event.

        The title is refused when it carries a secret-shaped value or exceeds
        500 characters. A duplicate idempotency_key is refused: one key means
        exactly one external effect, ever.
        """
        if not title:
            raise ValueError("task title must not be empty")
        if contains_secret(title):
            raise ValueError(f"refusing to create task: title {title!r} looks like it carries a secret")
        if len(title) > _TITLE_CAP:
            raise ValueError(f"task title exceeds the {_TITLE_CAP}-character limit")
        task_id = uuid.uuid4().hex
        ts = now_local()
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO tasks (task_id, kind, title, status, idempotency_key, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (task_id, kind, title, TaskStatus.CREATED.value, idempotency_key, ts, ts),
                )
                conn.execute(
                    "INSERT INTO task_events (task_id, from_status, to_status, reason, ts) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (task_id, "", TaskStatus.CREATED.value, "task created", ts),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"a task with idempotency key {idempotency_key!r} already exists; one key, one effect"
            ) from exc
        return self.get(task_id)

    def get(self, task_id: str) -> Task:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT task_id, kind, title, status, idempotency_key, waiting_reason, error, result, "
                "created_at, updated_at FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"no such task: {task_id!r}")
        return _row_to_task(row)

    def list(self, status: TaskStatus | None = None) -> list[Task]:
        """Tasks in creation order, optionally filtered to one status."""
        query = (
            "SELECT task_id, kind, title, status, idempotency_key, waiting_reason, error, result, "
            "created_at, updated_at FROM tasks"
        )
        params: tuple = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status.value,)
        query += " ORDER BY created_at, rowid"
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_task(row) for row in rows]

    def transition(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        reason: str | None = None,
        error: str | None = None,
        result: str | None = None,
    ) -> Task:
        """Move one task through the machine and append the history event.

        waiting_reason is kept only when the target is waiting_approval; error
        is kept only on failed; result only on completed. Terminal tasks refuse
        every transition.
        """
        with self._connection() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"no such task: {task_id!r}")
            current = TaskStatus(row[0])
            check_transition(current, target)
            ts = now_local()
            waiting_reason = _capped(reason, _TEXT_CAP) if target is TaskStatus.WAITING_APPROVAL else None
            conn.execute(
                "UPDATE tasks SET status = ?, waiting_reason = ?, error = ?, result = ?, updated_at = ? "
                "WHERE task_id = ?",
                (
                    target.value,
                    waiting_reason,
                    _capped(error, _TEXT_CAP) if target is TaskStatus.FAILED else None,
                    _capped(result, _TEXT_CAP) if target is TaskStatus.COMPLETED else None,
                    ts,
                    task_id,
                ),
            )
            conn.execute(
                "INSERT INTO task_events (task_id, from_status, to_status, reason, ts) VALUES (?, ?, ?, ?, ?)",
                (task_id, current.value, target.value, _capped(reason, _TEXT_CAP), ts),
            )
        return self.get(task_id)

    def history(self, task_id: str) -> "list[TaskEvent]":
        """The transition history of one task, oldest first.

        The annotation is a string on purpose: the class's own `list` method
        shadows the builtin inside the class body, so this def would otherwise
        try to subscript a function.
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT seq, task_id, from_status, to_status, reason, ts FROM task_events "
                "WHERE task_id = ? ORDER BY seq",
                (task_id,),
            ).fetchall()
        return [TaskEvent(seq=row[0], task_id=row[1], from_status=row[2], to_status=row[3], reason=row[4], ts=row[5]) for row in rows]

    def recover(self) -> "list[Task]":
        """Account for tasks the process left running; return the ones recovered.

        A task stuck in running after a restart goes to blocked with a reason
        naming the restart. waiting_approval is untouched: a human decision
        survives the restart and nothing auto-executes. A second recover on a
        clean store returns nothing.
        """
        recovered: list[Task] = []
        for task in self.list(status=TaskStatus.RUNNING):
            recovered.append(
                self.transition(task.task_id, TaskStatus.BLOCKED, reason="restart recovery: the process stopped while running")
            )
        return recovered

    def run_once(self, task_id: str, effect_key: str, effect: Callable[[], str]) -> str:
        """Run an external side effect at most once per (task, effect_key).

        The first call executes `effect` and records its outcome; a later call
        with the same key returns the recorded outcome without executing again.
        A failed attempt records nothing, so the retry re-executes — the ledger
        remembers successes, not attempts. The honest limit: a crash between
        the effect landing and the receipt being written can re-run it, the
        same at-least-once window any external effect has.
        """
        task = self.get(task_id)
        if task.status in TERMINAL_STATUSES:
            raise TaskStateError(f"task {task_id} is {task.status.value}; it takes no new effects")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT outcome FROM task_effects WHERE task_id = ? AND effect_key = ?",
                (task_id, effect_key),
            ).fetchone()
        if row is not None:
            return row[0]
        outcome = effect()
        recorded = _capped(outcome, _TEXT_CAP) or ""
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO task_effects (task_id, effect_key, outcome, ts) VALUES (?, ?, ?, ?)",
                (task_id, effect_key, recorded, now_local()),
            )
        return recorded
