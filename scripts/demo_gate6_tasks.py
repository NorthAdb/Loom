"""Gate 6 demo: the task state machine and the Context OS over the real stores.

Run with `.venv/Scripts/python scripts/demo_gate6_tasks.py`. Keyless: every
scenario runs against real stores in a temp data dir. Four scenarios:

(a) a task walks created -> running -> waiting_approval -> completed across a
    store reopen, so the history survives the restart;
(b) a task caught mid-running by a restart recovers to blocked, and a task in
    waiting_approval stays waiting (a human decision never auto-executes);
(c) an external side effect with an idempotency key runs exactly once across
    retries, and a failed attempt retries cleanly;
(d) turns land in the episodic log, compact folds them into the digest, and
    the injected context block shows digest, episodic, and feedback with the
    facts note still first.

Nothing is written outside temp dirs.
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loom.memory import EpisodicStore, FactsStore, FeedbackStore, build_context_note, compact
from loom.tasks import TaskStateError, TaskStatus, TaskStore


def line(title):
    print(f"\n=== {title} ===")


def scenario_a(data_dir: Path) -> None:
    line("(a) task lifecycle across a store reopen")
    store = TaskStore(data_dir)
    task = store.create("triage", "Draft the landlord reply", idempotency_key="demo-draft-1")
    print(f"created: {task.task_id} status={task.status.value}")
    task = store.transition(task.task_id, TaskStatus.RUNNING)
    task = store.transition(task.task_id, TaskStatus.WAITING_APPROVAL, reason="draft needs a human")
    print(f"paused:  status={task.status.value} waiting={task.waiting_reason}")

    reopened = TaskStore(data_dir)
    resumed = reopened.get(task.task_id)
    print(f"reopened: status={resumed.status.value} (the human decision survived the restart)")
    if resumed.status is not TaskStatus.WAITING_APPROVAL:
        raise SystemExit("scenario a failed: waiting_approval did not survive the reopen")
    resumed = reopened.transition(resumed.task_id, TaskStatus.RUNNING)
    resumed = reopened.transition(resumed.task_id, TaskStatus.COMPLETED, result="draft stored in the mailbox")
    print(f"completed: result={resumed.result}")
    for event in reopened.history(task.task_id):
        print(f"  history: {event.from_status or '-'} -> {event.to_status}")
    try:
        reopened.transition(task.task_id, TaskStatus.RUNNING)
        raise SystemExit("scenario a failed: a terminal task accepted a transition")
    except TaskStateError:
        print("terminal task refuses further transitions: ok")


def scenario_b(data_dir: Path) -> None:
    line("(b) restart recovery: running goes blocked, waiting stays waiting")
    store = TaskStore(data_dir)
    mid = store.transition(store.create("triage", "Mid-flight task").task_id, TaskStatus.RUNNING)
    waiting = store.transition(
        store.create("triage", "Waiting task").task_id, TaskStatus.WAITING_APPROVAL, reason="human"
    )

    after_restart = TaskStore(data_dir)
    recovered = after_restart.recover()
    print(f"recovered: {[task.task_id for task in recovered]} -> blocked")
    print(f"waiting task untouched: {after_restart.get(waiting.task_id).status.value}")
    if [task.task_id for task in recovered] != [mid.task_id]:
        raise SystemExit("scenario b failed: recovery missed the running task")
    if after_restart.get(waiting.task_id).status is not TaskStatus.WAITING_APPROVAL:
        raise SystemExit("scenario b failed: waiting_approval was auto-advanced")
    print("second recover is a no-op:", after_restart.recover() == [])


def scenario_c(data_dir: Path) -> None:
    line("(c) idempotency: one key, one external effect")
    store = TaskStore(data_dir)
    task = store.create("mail", "Create the mailbox draft")
    calls = []

    def fake_transport_append() -> str:
        calls.append(1)
        return "draft fake-draft-1 appended"

    first = store.run_once(task.task_id, "imap-append:demo-draft-1", fake_transport_append)
    second = store.run_once(task.task_id, "imap-append:demo-draft-1", fake_transport_append)
    print(f"effect outcome: {first}")
    print(f"transport calls after a replay with the same key: {len(calls)}")
    if len(calls) != 1 or first != second:
        raise SystemExit("scenario c failed: the effect ran twice for one key")

    attempts = []

    def flaky_transport() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("imap connection dropped")
        return "draft fake-draft-2 appended"

    try:
        store.run_once(task.task_id, "imap-append:demo-draft-2", flaky_transport)
    except RuntimeError:
        print("failed attempt recorded nothing; the retry re-executes")
    outcome = store.run_once(task.task_id, "imap-append:demo-draft-2", flaky_transport)
    print(f"retry outcome: {outcome} (attempts: {len(attempts)})")


def scenario_d(data_dir: Path) -> None:
    line("(d) context OS: episodic, feedback, compact, and the injected block")
    FactsStore(data_dir).upsert("reply_language", "中文")
    episodic = EpisodicStore(data_dir)
    episodic.append("turn", "asked what is on the calendar today")
    episodic.append("turn", "asked the landlord about the heating bill")
    FeedbackStore(data_dir).add("owner", "keep replies short")
    print(f"unfolded entries before compact: {episodic.count_unfolded()}")

    result = compact(data_dir)
    print(f"compact folded {result.folded} entries; digest now {len(result.new_digest)} chars")
    print(f"unfolded after compact: {episodic.count_unfolded()}")

    block = build_context_note(data_dir, entry_cap=6)
    print("injected context block:")
    for entry_line in block.splitlines():
        print(f"  {entry_line}")
    if "[digest]" not in block or "[feedback]" not in block:
        raise SystemExit("scenario d failed: the context block is missing pieces")
    facts = FactsStore(data_dir).list()
    print(f"facts after everything: {[fact.key for fact in facts]} (compact never writes facts)")


def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="loom-gate6-demo-"))
    print(f"data dir: {data_dir}")
    try:
        scenario_a(data_dir)
        scenario_b(data_dir)
        scenario_c(data_dir)
        scenario_d(data_dir)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    print("\nGate 6 demo: all scenarios behaved as the state machine and Context OS promise.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
