"""CLI tests for the Gate 6 task and context commands through main(argv)."""

import loom.cli
from loom.memory import EpisodicStore, FeedbackStore
from loom.tasks import TaskStatus, TaskStore


def run(capsys, argv):
    code = loom.cli.main(argv)
    return code, capsys.readouterr().out


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))


def test_tasks_list_show_recover_round_trip(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    store = TaskStore(tmp_path)
    task = store.transition(store.create("triage", "Draft the landlord reply").task_id, TaskStatus.RUNNING)

    code, out = run(capsys, ["tasks", "list"])
    assert code == 0
    assert task.task_id in out and "status=running" in out and "Draft the landlord reply" in out

    code, out = run(capsys, ["tasks", "list", "--status", "running"])
    assert code == 0 and task.task_id in out
    code, out = run(capsys, ["tasks", "list", "--status", "completed"])
    assert code == 0 and "(no tasks)" in out

    code, out = run(capsys, ["tasks", "show", task.task_id[:8]])
    assert code == 0
    assert "status=running" in out
    assert "- -> created" in out and "created -> running" in out

    code, out = run(capsys, ["tasks", "recover"])
    assert code == 0
    assert "is now blocked" in out
    assert TaskStore(tmp_path).get(task.task_id).status is TaskStatus.BLOCKED

    code, out = run(capsys, ["tasks", "recover"])
    assert code == 0 and "(nothing to recover)" in out


def test_tasks_show_by_prefix_and_refusals(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    store = TaskStore(tmp_path)
    task = store.create("triage", "First task")

    code, out = run(capsys, ["tasks", "show", task.task_id[:4]])
    assert code == 0 and "First task" in out

    code, out = run(capsys, ["tasks", "show", "zzzz"])
    assert code == 1 and "no task matches" in out

    code, out = run(capsys, ["tasks", "show", ""])
    assert code == 1


def test_memory_digest_episodic_feedback_compact_commands(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)

    code, out = run(capsys, ["memory", "digest"])
    assert code == 0 and "no digest yet" in out

    EpisodicStore(tmp_path).append("turn", "asked about the calendar")
    code, out = run(capsys, ["memory", "episodic"])
    assert code == 0 and "asked about the calendar" in out

    code, out = run(capsys, ["memory", "feedback", "add", "keep", "replies", "short"])
    assert code == 0 and "keep replies short" in out
    code, out = run(capsys, ["memory", "feedback", "list"])
    assert code == 0 and "keep replies short" in out

    code, out = run(capsys, ["memory", "compact"])
    assert code == 0 and "folded 1 entry" in out
    code, out = run(capsys, ["memory", "digest"])
    assert code == 0 and "asked about the calendar" in out
    code, out = run(capsys, ["memory", "compact"])
    assert code == 0 and "(nothing to compact)" in out

    code, out = run(capsys, ["memory", "episodic"])
    assert code == 0 and "(no episodic entries)" in out
    code, out = run(capsys, ["memory", "episodic", "--all"])
    assert code == 0 and "folded" in out


def test_memory_feedback_refuses_secrets(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)

    code, out = run(capsys, ["memory", "feedback", "add", "the key is", "ghp_0123456789abcdefghijklmnopqrstuv"])
    assert code == 1 and "carries a secret" in out
    assert FeedbackStore(tmp_path).recent() == []


def test_doctor_reports_the_gate6_stores(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOOM_ENV=dev\n", encoding="utf-8")

    code = loom.cli.main(["doctor"])
    out = capsys.readouterr().out

    assert code in (0, 1)  # .env/config lines decide the code, not the stores
    assert "tasks store" in out and "FAIL" not in out
    assert "context store" in out
