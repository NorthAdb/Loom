"""The graph under the gateway: the gate never shows a forbidden proposal, and execution re-checks."""

import json

from conftest import ScriptedChatModel, tool_call_msg
from langchain_core.messages import AIMessage
from langgraph.types import Command

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.gateway import ScopeStore, issue_scope
from loom.gateway.tool_gateway import ToolGateway


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


def _read_audit(data_dir):
    path = next((data_dir / "audit").glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _build(tmp_path, scripted, scope_actions):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    model = RecordingChatModel(scripted)
    store = ScopeStore(tmp_path)
    gateway = ToolGateway(AuditLog(tmp_path), store)
    graph = build_graph(model, settings, gateway=gateway)
    scope = issue_scope("owner", "sim_mail", "sandbox", scope_actions, ttl_seconds=None)
    store.add(scope)
    return graph, model, scope, gateway, store


def test_allowed_side_effect_gates_then_executes_with_the_correlation_id(tmp_path):
    graph, _model, scope, _gateway, _store = _build(
        tmp_path,
        [tool_call_msg("write_file", {"path": "note.txt", "content": "hello"}, "call_1"), AIMessage(content="done")],
        ["write_file"],
    )
    config = {"configurable": {"thread_id": "t1"}}

    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "write it"}], "run_id": "r1", "scope_id": scope.scope_id},
        config,
    )
    assert "__interrupt__" in paused

    graph.invoke(Command(resume={"decision": "approve"}), config)

    assert (tmp_path / "sandbox" / "note.txt").read_text(encoding="utf-8") == "hello"
    records = _read_audit(tmp_path)
    # Two gate authorizations are the truth of an interrupt: the gate node
    # re-runs on resume, so the pre-check fires before the pause and again
    # after it; the third authorization is run_tools' execution-time re-check.
    assert [record["decision"] for record in records] == [
        "authorized",
        "authorized",
        "approved",
        "authorized",
        "executed",
    ]
    assert all(record.get("correlation_id") == scope.correlation_id for record in records)


def test_forbidden_side_effect_never_reaches_the_human(tmp_path):
    graph, _model, scope, _gateway, _store = _build(
        tmp_path,
        [tool_call_msg("write_file", {"path": "note.txt", "content": "hello"}, "call_1")],
        ["echo"],
    )
    config = {"configurable": {"thread_id": "t1"}}

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "write it"}], "run_id": "r1", "scope_id": scope.scope_id},
        config,
    )

    assert "__interrupt__" not in result
    assert "Gateway refused write_file" in result["messages"][-1].content
    assert not (tmp_path / "sandbox" / "note.txt").exists()
    records = _read_audit(tmp_path)
    assert records[0]["decision"] == "rejected" and records[0]["actor"] == "gateway"
    assert records[0]["correlation_id"] == scope.correlation_id
    # The human never approved anything: no actor=human row exists.
    assert all(record["actor"] != "human" for record in records)


def test_scope_expiring_between_approval_and_execution_fails_closed(tmp_path, monkeypatch):
    graph, _model, scope, _gateway, store = _build(
        tmp_path,
        [tool_call_msg("write_file", {"path": "note.txt", "content": "hello"}, "call_1"), AIMessage(content="done")],
        ["write_file"],
    )
    config = {"configurable": {"thread_id": "t1"}}
    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "write it"}], "run_id": "r1", "scope_id": scope.scope_id},
        config,
    )
    assert "__interrupt__" in paused

    from datetime import datetime, timedelta

    stale = issue_scope(
        "owner", "sim_mail", "sandbox", ["write_file"], expires_at=(datetime.now().astimezone() - timedelta(seconds=1)).isoformat(), ttl_seconds=None
    )
    real_get = ScopeStore(tmp_path).get

    def expired_on_second_call(scope_id):
        if scope_id == scope.scope_id:
            return stale
        return real_get(scope_id)

    monkeypatch.setattr(store, "get", expired_on_second_call)

    result = graph.invoke(Command(resume={"decision": "approve"}), config)

    # The stale scope is caught at whichever check runs first after the
    # expiry (the gate re-run on resume); either way nothing executes.
    assert not (tmp_path / "sandbox" / "note.txt").exists()
    assert "Gateway refused write_file" in result["messages"][-1].content
    assert "expired" in result["messages"][-1].content
    assert all(record["decision"] != "executed" for record in _read_audit(tmp_path))


def test_a_run_without_a_scope_id_is_refused_by_the_gateway(tmp_path):
    graph, _model, _scope, _gateway, _store = _build(
        tmp_path,
        [tool_call_msg("write_file", {"path": "note.txt", "content": "hello"}, "call_1")],
        ["write_file"],
    )
    config = {"configurable": {"thread_id": "t1"}}

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "write it"}], "run_id": "r1"},
        config,
    )

    assert "Gateway refused write_file" in result["messages"][-1].content
    assert "no scope is attached" in _read_audit(tmp_path)[0]["args"]
