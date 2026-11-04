"""Behavior contracts for the task state machine and its store."""

import sqlite3

import pytest

from loom.tasks import TERMINAL_STATUSES, TaskStateError, TaskStatus, TaskStore


def test_created_task_starts_in_the_created_state(tmp_path):
    task = TaskStore(tmp_path).create("triage", "Draft a reply to the landlord")

    assert task.status is TaskStatus.CREATED
    assert task.kind == "triage"
    assert task.title == "Draft a reply to the landlord"
    assert task.idempotency_key is None


def test_transition_walks_the_happy_path_and_records_history(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("triage", "Draft a reply")
    task = store.transition(task.task_id, TaskStatus.RUNNING)
    task = store.transition(task.task_id, TaskStatus.WAITING_APPROVAL, reason="draft needs a human")
    task = store.transition(task.task_id, TaskStatus.RUNNING)
    task = store.transition(task.task_id, TaskStatus.COMPLETED, result="draft stored")

    assert task.status is TaskStatus.COMPLETED
    assert task.result == "draft stored"
    assert task.waiting_reason is None
    events = store.history(task.task_id)
    assert [(event.from_status, event.to_status) for event in events] == [
        ("", "created"),
        ("created", "running"),
        ("running", "waiting_approval"),
        ("waiting_approval", "running"),
        ("running", "completed"),
    ]
    waiting_event = events[2]
    assert waiting_event.reason == "draft needs a human"


@pytest.mark.parametrize(
    "current, target",
    [
        (TaskStatus.CREATED, TaskStatus.COMPLETED),
        (TaskStatus.CREATED, TaskStatus.FAILED),
        (TaskStatus.WAITING_APPROVAL, TaskStatus.COMPLETED),
        (TaskStatus.WAITING_APPROVAL, TaskStatus.BLOCKED),
        (TaskStatus.BLOCKED, TaskStatus.COMPLETED),
        (TaskStatus.COMPLETED, TaskStatus.RUNNING),
    ],
)
def test_transition_refuses_paths_outside_the_machine(tmp_path, current, target):
    store = TaskStore(tmp_path)
    task = store.create("triage", "Draft a reply")
    if current is TaskStatus.RUNNING:
        task = store.transition(task.task_id, TaskStatus.RUNNING)
    elif current is TaskStatus.WAITING_APPROVAL:
        task = store.transition(task.task_id, TaskStatus.WAITING_APPROVAL, reason="human")
    elif current is TaskStatus.BLOCKED:
        task = store.transition(task.task_id, TaskStatus.RUNNING)
        task = store.transition(task.task_id, TaskStatus.BLOCKED)
    elif current is TaskStatus.COMPLETED:
        task = store.transition(task.task_id, TaskStatus.RUNNING)
        task = store.transition(task.task_id, TaskStatus.COMPLETED, result="done")
    elif current is TaskStatus.FAILED:
        task = store.transition(task.task_id, TaskStatus.RUNNING)
        task = store.transition(task.task_id, TaskStatus.FAILED, error="boom")

    with pytest.raises(TaskStateError, match="illegal task transition"):
        store.transition(task.task_id, target)


def test_every_terminal_status_refuses_every_transition(tmp_path):
    store = TaskStore(tmp_path)
    paths = {
        TaskStatus.COMPLETED: [(TaskStatus.RUNNING,), (TaskStatus.FAILED,)],
        TaskStatus.FAILED: [(TaskStatus.RUNNING,), (TaskStatus.CANCELLED,)],
        TaskStatus.CANCELLED: [(TaskStatus.RUNNING,)],
    }
    for terminal, moves in paths.items():
        task = store.create("triage", "Draft a reply")
        task = store.transition(task.task_id, TaskStatus.RUNNING)
        task = store.transition(task.task_id, terminal)
        for move in moves:
            with pytest.raises(TaskStateError):
                store.transition(task.task_id, move[0])
    assert TERMINAL_STATUSES == {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


def test_waiting_approval_keeps_its_reason_and_survives_a_reopen(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("triage", "Draft a reply")
    task = store.transition(task.task_id, TaskStatus.RUNNING)
    task = store.transition(task.task_id, TaskStatus.WAITING_APPROVAL, reason="draft needs a human")

    reopened = TaskStore(tmp_path)
    loaded = reopened.get(task.task_id)
    assert loaded.status is TaskStatus.WAITING_APPROVAL
    assert loaded.waiting_reason == "draft needs a human"
    # The restart never advances the task on its own: it stays waiting, not completed.
    assert reopened.recover() == []
    assert reopened.get(task.task_id).status is TaskStatus.WAITING_APPROVAL


def test_recover_moves_running_tasks_to_blocked_and_is_idempotent(tmp_path):
    store = TaskStore(tmp_path)
    running = store.transition(store.create("triage", "Mid-run task").task_id, TaskStatus.RUNNING)
    waiting = store.transition(
        store.create("triage", "Waiting task").task_id, TaskStatus.WAITING_APPROVAL, reason="human"
    )

    reopened = TaskStore(tmp_path)
    recovered = reopened.recover()

    assert [task.task_id for task in recovered] == [running.task_id]
    blocked = reopened.get(running.task_id)
    assert blocked.status is TaskStatus.BLOCKED
    assert blocked.waiting_reason is None
    assert "restart recovery" in reopened.history(running.task_id)[-1].reason
    assert reopened.get(waiting.task_id).status is TaskStatus.WAITING_APPROVAL
    assert reopened.recover() == []


def test_blocked_task_returns_to_running(tmp_path):
    store = TaskStore(tmp_path)
    task = store.transition(store.create("triage", "Task").task_id, TaskStatus.RUNNING)
    task = store.transition(task.task_id, TaskStatus.BLOCKED, reason="waiting on the calendar")
    task = store.transition(task.task_id, TaskStatus.RUNNING)

    assert task.status is TaskStatus.RUNNING
    assert task.waiting_reason is None


def test_create_refuses_secret_titles_and_empty_titles(tmp_path):
    store = TaskStore(tmp_path)
    with pytest.raises(ValueError, match="carries a secret"):
        store.create("triage", "token sk-0123456789abcdefghijklmnop")
    with pytest.raises(ValueError, match="must not be empty"):
        store.create("triage", "")
    with pytest.raises(ValueError, match="500-character limit"):
        store.create("triage", "x" * 501)


def test_create_refuses_a_duplicate_idempotency_key(tmp_path):
    store = TaskStore(tmp_path)
    store.create("triage", "First", idempotency_key="effect-1")

    with pytest.raises(ValueError, match="one key, one effect"):
        store.create("triage", "Second", idempotency_key="effect-1")


def test_run_once_executes_the_effect_exactly_once(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("triage", "Create the draft")
    calls = []

    def fake_transport_create() -> str:
        calls.append(1)
        return "draft fake-draft-1 created"

    first = store.run_once(task.task_id, "imap-append", fake_transport_create)
    second = store.run_once(task.task_id, "imap-append", fake_transport_create)

    assert first == "draft fake-draft-1 created"
    assert second == first
    assert len(calls) == 1
    assert store.get(task.task_id).status is TaskStatus.CREATED


def test_run_once_retries_after_a_failure_and_keys_are_independent(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("triage", "Create the draft")
    attempts = []

    def flaky_transport() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("imap connection dropped")
        return "draft fake-draft-2 created"

    with pytest.raises(RuntimeError, match="connection dropped"):
        store.run_once(task.task_id, "imap-append", flaky_transport)
    assert store.run_once(task.task_id, "imap-append", flaky_transport) == "draft fake-draft-2 created"
    assert len(attempts) == 2
    assert store.run_once(task.task_id, "second-effect", lambda: "other effect") == "other effect"


def test_run_once_refuses_terminal_tasks(tmp_path):
    store = TaskStore(tmp_path)
    task = store.transition(store.create("triage", "Task").task_id, TaskStatus.RUNNING)
    store.transition(task.task_id, TaskStatus.COMPLETED, result="done")

    with pytest.raises(TaskStateError, match="takes no new effects"):
        store.run_once(task.task_id, "imap-append", lambda: "nope")


def test_get_unknown_task_raises_keyerror(tmp_path):
    store = TaskStore(tmp_path)
    with pytest.raises(KeyError):
        store.get("missing")

    with pytest.raises(KeyError):
        store.transition("missing", TaskStatus.RUNNING)


def test_list_filters_by_status_and_orders_by_creation(tmp_path):
    store = TaskStore(tmp_path)
    first = store.create("triage", "First")
    second = store.create("triage", "Second")
    store.transition(first.task_id, TaskStatus.RUNNING)

    assert [task.task_id for task in store.list()] == [first.task_id, second.task_id]
    assert [task.task_id for task in store.list(status=TaskStatus.RUNNING)] == [first.task_id]
    assert store.list(status=TaskStatus.COMPLETED) == []


def test_stored_records_stay_bounded(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("triage", "Task")
    long_reason = "y" * 5000
    store.transition(task.task_id, TaskStatus.WAITING_APPROVAL, reason=long_reason)

    assert len(store.get(task.task_id).waiting_reason) == 2000


def test_connection_rollback_keeps_the_store_consistent(tmp_path):
    """A failed transition leaves the task where it was, with no history row."""
    store = TaskStore(tmp_path)
    task = store.create("triage", "Task")
    with pytest.raises(TaskStateError):
        store.transition(task.task_id, TaskStatus.COMPLETED)

    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        events = conn.execute("SELECT COUNT(*) FROM task_events WHERE task_id = ?", (task.task_id,)).fetchone()[0]
    assert events == 1
    assert store.get(task.task_id).status is TaskStatus.CREATED
