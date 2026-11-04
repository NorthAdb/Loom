"""open_graph carries the gateway: unscoped runs keep the human gate, scoped runs are re-checked."""

from conftest import ScriptedChatModel, tool_call_msg
from langchain_core.messages import AIMessage
from langgraph.types import Command

from loom.core.config import Settings
from loom.core.runtime import open_graph
from loom.gateway import ScopeStore, issue_scope


class RecordingChatModel:
    def __init__(self, scripted):
        self.inner = ScriptedChatModel(messages=iter(scripted))
        self.seen = []

    def bind_tools(self, tools, **kwargs):
        self.inner.bind_tools(tools, **kwargs)
        return self

    def invoke(self, input, config=None, **kwargs):
        self.seen.append(list(input))
        return self.inner.invoke(input, config, **kwargs)


def _settings(tmp_path):
    return Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")


def test_an_unscoped_run_through_open_graph_keeps_the_human_gate_with_no_scope_rows(tmp_path):
    settings = _settings(tmp_path)
    model = RecordingChatModel(
        [tool_call_msg("write_file", {"path": "note.txt", "content": "hello"}, "c1"), AIMessage(content="done")]
    )

    import unittest.mock

    with unittest.mock.patch("loom.core.runtime.make_model", return_value=model), open_graph(settings) as graph:
        paused = graph.invoke(
            {"messages": [{"role": "user", "content": "write it"}], "run_id": "r1"},
            {"configurable": {"thread_id": "t1"}},
        )
        assert "__interrupt__" in paused  # the human gate, not a gateway refusal
        graph.invoke(Command(resume={"decision": "approve"}), {"configurable": {"thread_id": "t1"}})

    assert (tmp_path / "sandbox" / "note.txt").read_text(encoding="utf-8") == "hello"
    # Unscoped runs produce no gateway authorization rows.
    audit_text = next((tmp_path / "audit").glob("*.jsonl")).read_text(encoding="utf-8")
    assert "gateway" not in audit_text


def test_a_scoped_run_through_open_graph_is_fully_checked(tmp_path):
    settings = _settings(tmp_path)
    model = RecordingChatModel([tool_call_msg("write_file", {"path": "note.txt", "content": "hello"}, "c1")])
    scope = issue_scope("owner", "sim_mail", "sandbox", ["echo"], ttl_seconds=None)
    ScopeStore(tmp_path).add(scope)

    import unittest.mock

    with unittest.mock.patch("loom.core.runtime.make_model", return_value=model), open_graph(settings) as graph:
        result = graph.invoke(
            {"messages": [{"role": "user", "content": "write it"}], "run_id": "r1", "scope_id": scope.scope_id},
            {"configurable": {"thread_id": "t1"}},
        )

    assert "Gateway refused write_file" in result["messages"][-1].content
    assert not (tmp_path / "sandbox" / "note.txt").exists()
    audit_text = next((tmp_path / "audit").glob("*.jsonl")).read_text(encoding="utf-8")
    assert '"actor": "gateway"' in audit_text
