import json

from conftest import ScriptedChatModel, read_audit, tool_call_msg
from langchain_core.messages import AIMessage

import loom.runtime
from loom.config import Settings
from loom.pending import PendingStore
from loom.routines import Routine, RoutineStore
from loom.runner import resume_pending, run_routine


def make_settings(tmp_path):
    return Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")


def make_stores(tmp_path):
    return RoutineStore(tmp_path), PendingStore(tmp_path)


def add_write_routine(tmp_path, routine_id="daily_tidy"):
    store, pending = make_stores(tmp_path)
    routine = store.add(Routine(id=routine_id, name="Tidy", cron="0 9 * * *", prompt="write the file"))
    return routine, store, pending


def trigger_records(data_dir):
    return [record for record in read_audit(data_dir) if record["tool"] == "routine_trigger"]


def test_read_only_routine_completes_and_audits_the_trigger(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    store, pending = make_stores(tmp_path)
    routine = store.add(Routine(id="daily_stats", name="Stats", cron="30 8 * * *", prompt="count the files"))
    monkeypatch.setattr(
        loom.runtime, "make_model", lambda s: ScriptedChatModel(messages=iter([AIMessage(content="10 files total")]))
    )
    details = {}

    outcome = run_routine(routine, settings, store, pending, details=details)

    assert outcome == "completed"
    assert details["thread_id"].startswith("routine:daily_stats:")
    assert "pending_id" not in details
    triggered = trigger_records(tmp_path)
    assert len(triggered) == 1
    assert (triggered[0]["actor"], triggered[0]["decision"]) == ("routine", "triggered")
    args = json.loads(triggered[0]["args"])
    assert args == {"routine_id": "daily_stats", "thread_id": details["thread_id"]}
    stored = store.get("daily_stats")
    assert stored.last_status == "completed"
    assert stored.last_run_at


def test_write_routine_pends_and_writes_nothing(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    routine, store, pending = add_write_routine(tmp_path)
    monkeypatch.setattr(
        loom.runtime,
        "make_model",
        lambda s: ScriptedChatModel(
            messages=iter([tool_call_msg("write_file", {"path": "todo.md", "content": "hi"}), AIMessage(content="written")])
        ),
    )
    details = {}

    outcome = run_routine(routine, settings, store, pending, details=details)

    assert outcome == "pending"
    assert not (tmp_path / "sandbox" / "todo.md").exists()
    waiting = pending.list_pending()
    assert len(waiting) == 1
    record = waiting[0]
    assert record.routine_id == "daily_tidy"
    assert record.thread_id == details["thread_id"]
    assert record.status == "pending"
    assert record.tools[0]["name"] == "write_file"
    assert record.reason
    assert record.pending_id == details["pending_id"]
    assert store.get("daily_tidy").last_status == "pending"


def test_approve_resume_over_a_fresh_graph_executes(monkeypatch, tmp_path):
    """The resume opens its own checkpointer connection, so it works after a worker restart."""
    settings = make_settings(tmp_path)
    routine, store, pending = add_write_routine(tmp_path)
    monkeypatch.setattr(
        loom.runtime,
        "make_model",
        lambda s: ScriptedChatModel(
            messages=iter([tool_call_msg("write_file", {"path": "todo.md", "content": "hi"})])
        ),
    )
    details = {}
    assert run_routine(routine, settings, store, pending, details=details) == "pending"
    pending_id = details["pending_id"]

    # A separate scripted model proves the resumed run does not reuse the first graph's model.
    monkeypatch.setattr(
        loom.runtime, "make_model", lambda s: ScriptedChatModel(messages=iter([AIMessage(content="file written")]))
    )
    answer = resume_pending(pending_id, "approve", settings, pending)

    assert answer == "file written"
    assert (tmp_path / "sandbox" / "todo.md").read_text(encoding="utf-8") == "hi"
    decisions = [(record["tool"], record["decision"]) for record in read_audit(tmp_path)]
    assert ("routine_trigger", "triggered") in decisions
    assert ("write_file", "approved") in decisions
    assert ("write_file", "executed") in decisions
    assert pending.get(pending_id).status == "completed"
    assert pending.list_pending() == []
    assert store.get("daily_tidy").last_status == "completed"


def test_reject_resume_executes_nothing(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    routine, store, pending = add_write_routine(tmp_path)
    monkeypatch.setattr(
        loom.runtime,
        "make_model",
        lambda s: ScriptedChatModel(
            messages=iter([tool_call_msg("write_file", {"path": "todo.md", "content": "hi"})])
        ),
    )
    details = {}
    assert run_routine(routine, settings, store, pending, details=details) == "pending"

    monkeypatch.setattr(loom.runtime, "make_model", lambda s: ScriptedChatModel(messages=iter([])))
    answer = resume_pending(details["pending_id"], "reject", settings, pending)

    assert "rejected" in answer
    assert not (tmp_path / "sandbox" / "todo.md").exists()
    decisions = [(record["tool"], record["decision"]) for record in read_audit(tmp_path)]
    assert ("write_file", "rejected") in decisions
    assert ("write_file", "executed") not in decisions
    assert pending.get(details["pending_id"]).status == "completed"


def test_resumed_run_that_hits_the_gate_again_stays_pending(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    routine, store, pending = add_write_routine(tmp_path)
    monkeypatch.setattr(
        loom.runtime,
        "make_model",
        lambda s: ScriptedChatModel(
            messages=iter([tool_call_msg("write_file", {"path": "a.txt", "content": "one"}, "call_a")])
        ),
    )
    details = {}
    assert run_routine(routine, settings, store, pending, details=details) == "pending"
    pending_id = details["pending_id"]

    monkeypatch.setattr(
        loom.runtime,
        "make_model",
        lambda s: ScriptedChatModel(
            messages=iter([tool_call_msg("write_file", {"path": "b.txt", "content": "two"}, "call_b")])
        ),
    )
    again = resume_pending(pending_id, "approve", settings, pending)

    assert again == "pending"
    assert (tmp_path / "sandbox" / "a.txt").exists()
    assert not (tmp_path / "sandbox" / "b.txt").exists()
    record = pending.get(pending_id)
    assert record.status == "pending"
    assert [tool["name"] for tool in record.tools] == ["write_file"]
    assert json.loads(record.tools[0]["args"]) == {"path": "b.txt", "content": "two"}

    monkeypatch.setattr(
        loom.runtime, "make_model", lambda s: ScriptedChatModel(messages=iter([AIMessage(content="done two")]))
    )
    answer = resume_pending(pending_id, "approve", settings, pending)

    assert answer == "done two"
    assert (tmp_path / "sandbox" / "b.txt").exists()
    assert pending.get(pending_id).status == "completed"


def test_resume_unknown_pending_id_raises_readable_keyerror(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)

    try:
        resume_pending("no-such-id", "approve", settings, PendingStore(tmp_path))
    except KeyError as exc:
        assert "no-such-id" in exc.args[0]
    else:
        raise AssertionError("unknown pending id did not raise")


def test_double_resume_raises_already(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    routine, store, pending = add_write_routine(tmp_path)
    monkeypatch.setattr(
        loom.runtime,
        "make_model",
        lambda s: ScriptedChatModel(
            messages=iter([tool_call_msg("write_file", {"path": "todo.md", "content": "hi"})])
        ),
    )
    details = {}
    assert run_routine(routine, settings, store, pending, details=details) == "pending"
    pending_id = details["pending_id"]
    monkeypatch.setattr(
        loom.runtime, "make_model", lambda s: ScriptedChatModel(messages=iter([AIMessage(content="file written")]))
    )
    assert resume_pending(pending_id, "approve", settings, pending) == "file written"

    try:
        resume_pending(pending_id, "approve", settings, pending)
    except ValueError as exc:
        assert "already" in str(exc)
    else:
        raise AssertionError("double resume did not raise")
