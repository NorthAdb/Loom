from conftest import ScriptedChatModel, read_audit, tool_call_msg
from langchain_core.messages import AIMessage

import loom.cli
import loom.runtime
from loom.pending import PendingStore
from loom.routines import Routine, RoutineStore


def setup_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))


def add_routine(monkeypatch, tmp_path, routine_id="daily_tidy"):
    setup_env(monkeypatch, tmp_path)
    code = loom.cli.main([
        "routines", "add", "--id", routine_id, "--name", "Tidy", "--cron", "0 9 * * *", "--prompt", "write the file",
    ])
    assert code == 0


def patch_model(monkeypatch, scripted):
    """Patch one shared model instance; a lambda that rebuilds it would restart the script on resume."""
    model = ScriptedChatModel(messages=iter(scripted))
    monkeypatch.setattr(loom.runtime, "make_model", lambda settings: model)
    return model


def test_routines_add_list_remove_through_the_cli(monkeypatch, tmp_path, capsys):
    setup_env(monkeypatch, tmp_path)

    assert loom.cli.main([
        "routines", "add", "--id", "daily_stats", "--name", "Daily stats",
        "--cron", "30 8 * * *", "--prompt", "count the sandbox files",
    ]) == 0
    stored = RoutineStore(tmp_path).get("daily_stats")
    assert stored.prompt == "count the sandbox files"
    assert "added routine daily_stats" in capsys.readouterr().out

    assert loom.cli.main(["routines", "list"]) == 0
    listed = capsys.readouterr().out
    assert "daily_stats" in listed
    assert "30 8 * * *" in listed
    assert "enabled=true" in listed

    assert loom.cli.main(["routines", "remove", "daily_stats"]) == 0
    assert RoutineStore(tmp_path).load() == []
    assert "removed routine daily_stats" in capsys.readouterr().out


def test_routines_add_rejects_duplicate_and_bad_cron_with_nonzero_exit(monkeypatch, tmp_path, capsys):
    setup_env(monkeypatch, tmp_path)

    assert loom.cli.main([
        "routines", "add", "--id", "daily_tidy", "--name", "Tidy", "--cron", "0 12 * * *", "--prompt", "x",
    ]) == 0
    capsys.readouterr()
    assert loom.cli.main([
        "routines", "add", "--id", "daily_tidy", "--name", "Dup", "--cron", "0 6 * * *", "--prompt", "x",
    ]) == 1
    assert "already exists" in capsys.readouterr().out

    assert loom.cli.main([
        "routines", "add", "--id", "broken", "--name", "Broken", "--cron", "99 99 * * *", "--prompt", "x",
    ]) == 1
    out = capsys.readouterr().out
    assert "99 99 * * *" in out
    assert [routine.id for routine in RoutineStore(tmp_path).load()] == ["daily_tidy"]


def test_read_only_run_prints_thread_and_completed(monkeypatch, tmp_path, capsys):
    add_routine(monkeypatch, tmp_path, "daily_stats")
    patch_model(monkeypatch, [AIMessage(content="10 files in the sandbox")])

    assert loom.cli.main(["routines", "run", "daily_stats"]) == 0
    out = capsys.readouterr().out
    assert "routine:daily_stats:" in out
    assert "completed" in out
    assert RoutineStore(tmp_path).get("daily_stats").last_status == "completed"


def test_write_run_pends_list_shows_it_and_approve_executes(monkeypatch, tmp_path, capsys):
    add_routine(monkeypatch, tmp_path)
    patch_model(
        monkeypatch,
        [
            tool_call_msg("write_file", {"path": "todo.md", "content": "hi"}),
            AIMessage(content="file written"),
        ],
    )

    assert loom.cli.main(["routines", "run", "daily_tidy"]) == 0
    run_out = capsys.readouterr().out
    assert "waiting for approval" in run_out
    pending_id = PendingStore(tmp_path).list_pending()[0].pending_id
    assert pending_id in run_out
    assert not (tmp_path / "sandbox" / "todo.md").exists()

    assert loom.cli.main(["approvals", "list"]) == 0
    listed = capsys.readouterr().out
    assert pending_id in listed
    assert "daily_tidy" in listed
    assert "write_file" in listed

    assert loom.cli.main(["approvals", "approve", pending_id]) == 0
    approved_out = capsys.readouterr().out
    assert "file written" in approved_out
    assert (tmp_path / "sandbox" / "todo.md").read_text(encoding="utf-8") == "hi"
    assert PendingStore(tmp_path).list_pending() == []

    assert loom.cli.main(["approvals", "list"]) == 0
    assert "(none)" in capsys.readouterr().out
    decisions = [(record["tool"], record["decision"]) for record in read_audit(tmp_path)]
    assert ("write_file", "approved") in decisions
    assert ("write_file", "executed") in decisions


def test_reject_resolves_pending_without_writing(monkeypatch, tmp_path, capsys):
    add_routine(monkeypatch, tmp_path)
    patch_model(monkeypatch, [tool_call_msg("write_file", {"path": "todo.md", "content": "hi"})])

    assert loom.cli.main(["routines", "run", "daily_tidy"]) == 0
    capsys.readouterr()
    pending_id = PendingStore(tmp_path).list_pending()[0].pending_id

    assert loom.cli.main(["approvals", "reject", pending_id]) == 0
    out = capsys.readouterr().out
    assert "rejected" in out
    assert not (tmp_path / "sandbox" / "todo.md").exists()
    decisions = [(record["tool"], record["decision"]) for record in read_audit(tmp_path)]
    assert ("write_file", "rejected") in decisions
    assert ("write_file", "executed") not in decisions


def test_approving_twice_and_unknown_ids_exit_nonzero(monkeypatch, tmp_path, capsys):
    add_routine(monkeypatch, tmp_path)
    patch_model(
        monkeypatch,
        [
            tool_call_msg("write_file", {"path": "todo.md", "content": "hi"}),
            AIMessage(content="file written"),
        ],
    )
    assert loom.cli.main(["routines", "run", "daily_tidy"]) == 0
    capsys.readouterr()
    pending_id = PendingStore(tmp_path).list_pending()[0].pending_id
    assert loom.cli.main(["approvals", "approve", pending_id]) == 0
    capsys.readouterr()

    assert loom.cli.main(["approvals", "approve", pending_id]) == 1
    assert "already" in capsys.readouterr().out
    assert loom.cli.main(["approvals", "approve", "no-such-id"]) == 1
    assert "no such pending approval" in capsys.readouterr().out


def test_run_disabled_routine_exits_nonzero(monkeypatch, tmp_path, capsys):
    setup_env(monkeypatch, tmp_path)
    RoutineStore(tmp_path).add(
        Routine(id="off", name="Off", cron="0 9 * * *", prompt="nothing", enabled=False)
    )

    assert loom.cli.main(["routines", "run", "off"]) == 1
    assert "routine is disabled: off" in capsys.readouterr().out
