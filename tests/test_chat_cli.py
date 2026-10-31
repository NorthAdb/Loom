from conftest import ScriptedChatModel, read_audit, tool_call_msg
from langchain_core.messages import AIMessage

import loom.cli
import loom.runtime
from loom.graph import build_graph


class Reader:
    """Scripted input; records every prompt the CLI printed."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def make_session_graph(tmp_path, scripted):
    settings = _settings(tmp_path)
    model = ScriptedChatModel(messages=iter(scripted))
    return build_graph(model, settings)


def _settings(tmp_path):
    from loom.config import Settings

    return Settings(loom_env="dev", loom_data_dir=tmp_path)


def audit_decisions(data_dir):
    return [record["decision"] for record in read_audit(data_dir)]


def test_approve_turn_executes_the_proposed_tool(tmp_path):
    scripted = [
        tool_call_msg("propose_sandbox_note", {"filename": "c.txt", "content": "cli note"}),
        AIMessage(content="note written"),
    ]
    graph = make_session_graph(tmp_path, scripted)
    reader = Reader(["save a note", "approve", "quit"])
    lines = []

    loom.cli.chat_session(graph, "t1", reader, lines.append)

    assert "you> " in reader.prompts
    assert "approve or reject? " in reader.prompts
    assert any("propose_sandbox_note" in line for line in lines)
    assert lines[-1] == "note written"
    assert (tmp_path / "notes" / "c.txt").read_text(encoding="utf-8") == "cli note"
    assert audit_decisions(tmp_path) == ["approved", "executed"]


def test_reject_turn_does_not_execute_and_explains(tmp_path):
    scripted = [tool_call_msg("propose_sandbox_note", {"filename": "d.txt", "content": "no"})]
    graph = make_session_graph(tmp_path, scripted)
    reader = Reader(["save a note", "reject", "quit"])
    lines = []

    loom.cli.chat_session(graph, "t2", reader, lines.append)

    assert "approve or reject? " in reader.prompts
    assert lines[-1] == "The proposed action (propose_sandbox_note) was rejected and was not executed."
    assert not (tmp_path / "notes").exists()
    assert audit_decisions(tmp_path) == ["rejected"]


def test_chat_command_builds_the_graph_through_the_cli_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))
    scripted = [
        tool_call_msg("propose_sandbox_note", {"filename": "e.txt", "content": "wired"}),
        AIMessage(content="done"),
    ]
    model = ScriptedChatModel(messages=iter(scripted))
    monkeypatch.setattr(loom.runtime, "make_model", lambda settings: model)
    reader = Reader(["write it", "approve", "quit"])
    lines = []

    code = loom.cli._chat(None, reader, lines.append)

    assert code == 0
    assert any(line.startswith("thread ") for line in lines)
    assert (tmp_path / "notes" / "e.txt").read_text(encoding="utf-8") == "wired"
    assert lines[-1] == "goodbye"
