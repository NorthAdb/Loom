"""The Gate 7 negative suite (handoff section 5): the boundary holds end to end.

Each test drives the real graph with the real gateway over a simulated
inbound message and proves one refusal. Console-side negatives (session,
CSRF, no-send control) live with the console tests.
"""

import json
from pathlib import Path

from conftest import ScriptedChatModel, tool_call_msg
from langchain_core.messages import AIMessage
from langgraph.types import Command

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.gateway import ScopeStore, issue_scope
from loom.gateway.tool_gateway import ToolGateway
from loom.inbound import InboxStore, accept_inbound, compose_triage_prompt
from loom.inbound.sim_mail import load_sim_mail
from loom.memory import FactsStore
from loom.tasks import TaskStore

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "inbound"


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


class FakeSemantic:
    def __init__(self):
        self.added = []

    def add(self, texts):
        self.added.extend(texts)

    def search(self, query, limit=3):
        return []


def _fixture(message_id):
    return next(message for message in load_sim_mail(FIXTURES_DIR) if message.message_id == message_id)


def _setup(tmp_path, scripted):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    model = RecordingChatModel(scripted)
    semantic = FakeSemantic()
    store = ScopeStore(tmp_path)
    gateway = ToolGateway(AuditLog(tmp_path), store)
    graph = build_graph(model, settings, memory=semantic, gateway=gateway)
    seams = {
        "tasks": TaskStore(tmp_path),
        "scopes": store,
        "audit": AuditLog(tmp_path),
        "inbox": InboxStore(tmp_path),
    }
    return graph, model, semantic, seams


def _accept(seams, message):
    task, scope = accept_inbound(
        message, tasks=seams["tasks"], scopes=seams["scopes"], audit=seams["audit"], inbox=seams["inbox"]
    )
    return task, scope


def _invoke(graph, scope, text, run_id="r1"):
    return graph.invoke(
        {"messages": [{"role": "user", "content": text}], "run_id": run_id, "scope_id": scope.scope_id if scope else None},
        {"configurable": {"thread_id": "t1"}},
    )


def _audit(data_dir):
    path = next((data_dir / "audit").glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_1_forged_owner_cannot_elevate(tmp_path):
    """An unauthenticated body that claims to be the owner gets no scope and no draft."""
    graph, _model, _semantic, seams = _setup(tmp_path, [tool_call_msg("draft_create_local", {"to": "boss@example.net", "subject": "transfer", "body": "money"}, "c1")])
    message = _fixture("sim-002")
    task, scope = _accept(seams, message)

    assert scope is None
    result = _invoke(graph, None, compose_triage_prompt(message))

    assert "Gateway refused draft_create_local" in result["messages"][-1].content
    gateway_rows = [record for record in _audit(tmp_path) if record["actor"] == "gateway"]
    assert gateway_rows and "no scope is attached" in gateway_rows[0]["args"]
    assert seams["tasks"].get(task.task_id).status.name == "RUNNING"  # the turn ran; the gate held
    assert (tmp_path / "drafts.json").exists() is False


def test_2_hidden_facts_write_fails(tmp_path):
    """A body that hides an operator instruction writes nothing into any memory."""
    graph, _model, semantic, seams = _setup(tmp_path, [AIMessage(content="Understood, I have saved that preference permanently.")])
    message = _fixture("sim-003")
    _task, scope = _accept(seams, message)

    result = _invoke(graph, scope, compose_triage_prompt(message))

    assert "saved that preference" in result["messages"][-1].content  # the model believed it
    assert semantic.added == []  # nothing reached semantic memory
    assert FactsStore(tmp_path).list() == []  # facts untouched
    from loom.memory import EpisodicStore

    assert EpisodicStore(tmp_path).count_unfolded() == 0  # the untrusted text is not re-injectable


def test_3_demand_to_send_fails(tmp_path):
    """There is no send tool: the demand resolves to an unknown-tool error, nothing more."""
    graph, _model, _semantic, seams = _setup(
        tmp_path,
        [
            tool_call_msg("send_mail", {"to": "boss@example.net", "subject": "transfer", "body": "money"}, "c1"),
            AIMessage(content="I cannot send email."),
        ],
    )
    message = _fixture("sim-002")
    _task, scope = _accept(seams, message)
    assert scope is None

    result = _invoke(graph, None, compose_triage_prompt(message))

    tool_messages = [m for m in result["messages"] if getattr(m, "type", "") == "tool"]
    assert any("unknown tool send_mail" in m.content for m in tool_messages)
    assert (tmp_path / "drafts.json").exists() is False


def test_4_expired_scope_is_refused_at_the_gate(tmp_path):
    graph, _model, _semantic, seams = _setup(tmp_path, [tool_call_msg("draft_create_local", {"to": "a@b.c", "subject": "s", "body": "b"}, "c1")])
    message = _fixture("sim-001")
    task, _scope = _accept(seams, message)
    expired = issue_scope(
        "owner",
        "sim_mail",
        f"inbound/sim_mail/{message.message_id}",
        ["draft_create_local"],
        expires_at="2020-01-01T00:00:00+08:00",
        ttl_seconds=None,
    )
    seams["scopes"].add(expired)

    result = _invoke(graph, expired, compose_triage_prompt(message))

    assert "Gateway refused draft_create_local" in result["messages"][-1].content
    assert "expired" in result["messages"][-1].content
    assert (tmp_path / "drafts.json").exists() is False
    assert seams["tasks"].get(task.task_id).status.name == "RUNNING"


def test_5_action_outside_the_allow_set_is_refused(tmp_path):
    """The owner scope drafts; it does not write sandbox files, whatever the model proposes."""
    graph, _model, _semantic, seams = _setup(tmp_path, [tool_call_msg("write_file", {"path": "evil.txt", "content": "x"}, "c1")])
    message = _fixture("sim-001")
    _task, scope = _accept(seams, message)

    result = _invoke(graph, scope, compose_triage_prompt(message))

    assert "not in the scope's allowed actions" in result["messages"][-1].content
    assert not (tmp_path / "sandbox" / "evil.txt").exists()


def test_6_the_model_cannot_mint_a_scope(tmp_path):
    """issue_scope is not a tool; the state's scope_id is caller-set and store-resolved."""
    graph, _model, _semantic, seams = _setup(
        tmp_path,
        [
            tool_call_msg("issue_scope", {"principal": "owner", "allowed_actions": ["send_mail"]}, "c1"),
            AIMessage(content="I cannot grant scopes."),
        ],
    )
    message = _fixture("sim-001")
    _task, scope = _accept(seams, message)

    result = _invoke(graph, scope, compose_triage_prompt(message))

    tool_messages = [m for m in result["messages"] if getattr(m, "type", "") == "tool"]
    assert any("unknown tool issue_scope" in m.content for m in tool_messages)
    # The store never grew beyond the one scope the accept step issued.
    assert len(seams["scopes"].list()) == 1
    assert seams["scopes"].list()[0].allowed_actions == {"draft_create_local"}


def test_7_audit_failure_means_no_side_effect(tmp_path, monkeypatch):
    graph, _model, _semantic, seams = _setup(tmp_path, [tool_call_msg("draft_create_local", {"to": "a@b.c", "subject": "s", "body": "b"}, "c1"), AIMessage(content="done")])
    message = _fixture("sim-001")
    _task, scope = _accept(seams, message)

    real_append = AuditLog.append

    def broken_append(self, **kwargs):
        raise OSError("audit disk full")

    monkeypatch.setattr(AuditLog, "append", broken_append)

    result = _invoke(graph, scope, compose_triage_prompt(message))

    assert "Gateway refused draft_create_local" in result["messages"][-1].content
    assert (tmp_path / "drafts.json").exists() is False
    monkeypatch.setattr(AuditLog, "append", real_append)
    assert _audit(tmp_path)  # the accept row was written before the failure was staged


def test_the_approved_happy_path_writes_one_local_draft(tmp_path):
    """The control: the same flow with the right scope, approved, writes exactly one draft."""
    graph, _model, _semantic, seams = _setup(
        tmp_path,
        [
            tool_call_msg("draft_create_local", {"to": "landlord@example.com", "subject": "Heating bill", "body": "We agree and will pay by the 15th."}, "c1"),
            AIMessage(content="Draft stored for your review."),
        ],
    )
    message = _fixture("sim-001")
    _task, scope = _accept(seams, message)
    config = {"configurable": {"thread_id": "t1"}}

    paused = _invoke(graph, scope, compose_triage_prompt(message))
    assert "__interrupt__" in paused
    graph.invoke(Command(resume={"decision": "approve"}), config)

    from loom.tools.drafts import DraftStore

    drafts = DraftStore(tmp_path).list()
    assert len(drafts) == 1 and drafts[0].status == "local_only"
    assert drafts[0].to == "landlord@example.com"
    correlation_ids = {record.get("correlation_id") for record in _audit(tmp_path)}
    assert scope.correlation_id in correlation_ids
