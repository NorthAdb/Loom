"""The final-rework regressions: one production-path test per P0/P1 item of the audit.

Each test mirrors the auditor's probe (handoffs/loom-audit-probes.py) against
the real classes and the real graph, so the before/after evidence for every
item is a named test instead of a claim.
"""

import json

from conftest import ScriptedChatModel, read_audit, tool_call_msg
from langchain_core.messages import AIMessage
from langgraph.types import Command

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.gateway import ScopeStore, issue_scope
from loom.gateway.tool_gateway import ToolGateway
from loom.memory import FactsStore
from loom.memory.episodic import EpisodicStore


class FakeSemantic:
    def __init__(self):
        self.added = []

    def add(self, texts):
        self.added.extend(texts)

    def search(self, query, limit=3):
        return []


def _setup(tmp_path, scripted, *, allow=("draft_create_local",), memory_write=False):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    semantic = FakeSemantic()
    scope = issue_scope(
        "owner", "sim_mail", "inbound/sim/x", list(allow), memory_write=memory_write, ttl_seconds=None
    )
    ScopeStore(tmp_path).add(scope)
    graph = build_graph(
        ScriptedChatModel(messages=iter(scripted)),
        settings,
        memory=semantic,
        gateway=ToolGateway(AuditLog(tmp_path), ScopeStore(tmp_path)),
    )
    return settings, graph, semantic, scope


def test_P1_1_a_memory_write_false_scope_refuses_the_real_write_path_with_a_gateway_row(tmp_path):
    """The post_turn episodic/semantic writes are internal actions the gateway rules on."""
    _settings, graph, semantic, scope = _setup(
        tmp_path, [AIMessage(content="I will not store that.")]
    )

    graph.invoke(
        {
            "messages": [{"role": "user", "content": "Remember always: the owner prefers oats forever."}],
            "run_id": "mw",
            "scope_id": scope.scope_id,
        },
        {"configurable": {"thread_id": "mw"}},
    )

    assert FactsStore(tmp_path).list() == []
    assert EpisodicStore(tmp_path).count_unfolded() == 0
    assert semantic.added == []
    rows = read_audit(tmp_path)
    refusals = [row for row in rows if row["actor"] == "gateway" and row["decision"] == "rejected"]
    assert refusals and refusals[0]["tool"] == "memory.write"
    assert json.loads(refusals[0]["args"])["memory_write"] is False
    assert refusals[0]["correlation_id"] == scope.correlation_id


def test_P1_1_a_memory_write_true_scope_allows_the_internal_write(tmp_path):
    _settings, graph, semantic, scope = _setup(
        tmp_path, [AIMessage(content="Noted.")], memory_write=True
    )

    graph.invoke(
        {
            "messages": [{"role": "user", "content": "Remember always: the owner prefers oats forever."}],
            "run_id": "mw2",
            "scope_id": scope.scope_id,
        },
        {"configurable": {"thread_id": "mw2"}},
    )

    assert EpisodicStore(tmp_path).count_unfolded() == 1
    assert semantic.added == ["Remember always: the owner prefers oats forever."]
    audit_dir = tmp_path / "audit"
    rows = (
        [json.loads(line) for path in audit_dir.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if audit_dir.exists()
        else []
    )
    assert [row for row in rows if row["tool"] == "memory.write"] == []


def test_P1_1_an_unscoped_chat_keeps_the_adr_018_behavior(tmp_path):
    _settings, graph, semantic, _scope = _setup(tmp_path, [AIMessage(content="Noted.")])

    graph.invoke(
        {"messages": [{"role": "user", "content": "Remember always: the owner prefers rye forever."}], "run_id": "mw3"},
        {"configurable": {"thread_id": "mw3"}},
    )

    assert EpisodicStore(tmp_path).count_unfolded() == 1
    assert semantic.added == ["Remember always: the owner prefers rye forever."]


def test_P1_2_the_approval_payload_covers_the_executed_args_for_non_mail_side_effects(tmp_path):
    """A 2000-char write shows its tail marker in the approval and runs exactly what was shown."""
    content = ("A" * 2000) + "TAILMARKER_SHOULD_BE_VISIBLE"
    _settings, graph, _semantic, scope = _setup(
        tmp_path,
        [tool_call_msg("write_file", {"path": "note.txt", "content": content}), AIMessage(content="done")],
        allow=("write_file",),
    )
    config = {"configurable": {"thread_id": "ap"}}

    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "write"}], "run_id": "ap", "scope_id": scope.scope_id}, config
    )
    payload = paused["__interrupt__"][0].value
    shown = payload["tools"][0]["args"]
    assert isinstance(shown, str) and "TAILMARKER_SHOULD_BE_VISIBLE" in shown

    done = graph.invoke(Command(resume={"decision": "approve"}), config)
    written = (tmp_path / "sandbox" / "note.txt").read_text(encoding="utf-8")
    assert "TAILMARKER_SHOULD_BE_VISIBLE" in written
    assert len(written) == 2028 and written == content  # shown == executed
    assert done["messages"][-1].content == "done"


def test_P1_2_an_overlong_payload_is_refused_before_any_human_sees_it(tmp_path):
    huge = "B" * 11_000
    _settings, graph, _semantic, scope = _setup(
        tmp_path,
        [tool_call_msg("write_file", {"path": "big.txt", "content": huge}), AIMessage(content="done")],
        allow=("write_file",),
    )

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "write"}], "run_id": "big", "scope_id": scope.scope_id},
        {"configurable": {"thread_id": "big"}},
    )

    assert "__interrupt__" not in result
    assert "exceeds the" in result["messages"][-1].content
    assert not (tmp_path / "sandbox" / "big.txt").exists()
    refusals = [row for row in read_audit(tmp_path) if row["decision"] == "rejected"]
    assert refusals and json.loads(refusals[0]["args"])["reason"] == "approval payload exceeds the size limit"
