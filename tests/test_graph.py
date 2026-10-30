import json

from conftest import ScriptedChatModel, tool_call_msg
from langchain_core.messages import AIMessage
from langgraph.types import Command

from loom.config import Settings
from loom.graph import build_graph


def read_audit(data_dir):
    path = next((data_dir / "audit").glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def make_graph(tmp_path, scripted):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path)
    model = ScriptedChatModel(messages=iter(scripted))
    return build_graph(model, settings), tmp_path


def test_harmless_tool_runs_without_gate_or_audit(tmp_path):
    graph, data_dir = make_graph(
        tmp_path,
        [tool_call_msg("get_time", {}), AIMessage(content="the time is in the tool result")],
    )

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "what time is it?"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert "__interrupt__" not in result
    assert result["messages"][-1].content == "the time is in the tool result"
    assert not (data_dir / "audit").exists()


def test_side_effect_tool_approve_executes_and_audits(tmp_path):
    graph, data_dir = make_graph(
        tmp_path,
        [tool_call_msg("propose_sandbox_note", {"filename": "a.txt", "content": "hi"}), AIMessage(content="note saved")],
    )
    config = {"configurable": {"thread_id": "t2"}}

    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "save a note"}], "run_id": "r2"}, config
    )
    payload = paused["__interrupt__"][0].value
    assert payload["tools"][0]["name"] == "propose_sandbox_note"
    assert not (data_dir / "notes" / "a.txt").exists()

    result = graph.invoke(Command(resume={"decision": "approve"}), config)

    assert (data_dir / "notes" / "a.txt").exists()
    assert result["messages"][-1].content == "note saved"
    decisions = [(r["actor"], r["decision"]) for r in read_audit(data_dir)]
    assert ("human", "approved") in decisions
    assert ("loom", "executed") in decisions


def test_side_effect_tool_reject_executes_nothing(tmp_path):
    graph, data_dir = make_graph(
        tmp_path,
        [tool_call_msg("propose_sandbox_note", {"filename": "b.txt", "content": "hi"})],
    )
    config = {"configurable": {"thread_id": "t3"}}

    graph.invoke({"messages": [{"role": "user", "content": "save a note"}], "run_id": "r3"}, config)
    result = graph.invoke(Command(resume={"decision": "reject"}), config)

    assert not (data_dir / "notes").exists()
    final = result["messages"][-1]
    assert isinstance(final, AIMessage)
    assert "rejected" in final.content
    decisions = [r["decision"] for r in read_audit(data_dir)]
    assert decisions == ["rejected"]
