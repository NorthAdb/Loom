"""The triage runner: an accepted inbox message -> scoped graph run -> pending -> decision.

Each test drives `run_triage` and `resolve_pending` against the real graph with
a scripted model, exactly the path the CLI's `loom triage` and the console's
Triage button take. The runner-level audit rows (trigger, skip, task settle)
are asserted alongside the graph's own chain rows.
"""

import json
from pathlib import Path

import pytest
from conftest import ScriptedChatModel, read_audit, tool_call_msg
from langchain_core.messages import AIMessage

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.core.runner import resolve_pending, run_triage
from loom.gateway import ScopeStore
from loom.gateway.pending import PendingStore
from loom.gateway.tool_gateway import ToolGateway
from loom.inbound import InboxStore, accept_inbound
from loom.inbound.sim_mail import load_sim_mail
from loom.tasks import TaskStatus, TaskStore
from loom.tools.drafts import DraftStore

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "inbound"


class FakeSemantic:
    def __init__(self):
        self.added = []

    def add(self, texts):
        self.added.extend(texts)

    def search(self, query, limit=3):
        return []


def _setup(tmp_path, scripted):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    graph = build_graph(
        ScriptedChatModel(messages=iter(scripted)),
        settings,
        memory=FakeSemantic(),
        gateway=ToolGateway(AuditLog(tmp_path), ScopeStore(tmp_path)),
    )
    seams = {
        "tasks": TaskStore(tmp_path),
        "scopes": ScopeStore(tmp_path),
        "audit": AuditLog(tmp_path),
        "inbox": InboxStore(tmp_path),
    }
    return settings, graph, seams


def _accept(seams, message_id):
    message = next(message for message in load_sim_mail(FIXTURES_DIR) if message.message_id == message_id)
    return accept_inbound(
        message, tasks=seams["tasks"], scopes=seams["scopes"], audit=seams["audit"], inbox=seams["inbox"]
    )


def test_triage_happy_path_pends_then_approve_writes_one_categorized_draft(tmp_path):
    settings, graph, seams = _setup(
        tmp_path,
        [
            tool_call_msg(
                "draft_create_local",
                {"to": "landlord@example.com", "subject": "Heating bill", "body": "We agree and will pay by the 15th.", "category": "reply"},
                "c1",
            ),
            AIMessage(content="Draft stored for your review."),
        ],
    )
    task, scope = _accept(seams, "sim-001")
    pending = PendingStore(tmp_path)

    outcome = run_triage("sim-001", settings, pending, graph=graph)

    assert outcome.outcome == "pending" and outcome.pending_id
    assert outcome.thread_id == f"triage:{task.task_id}"
    record = pending.get(outcome.pending_id)
    assert record.routine_id == "inbound_triage"
    assert record.scope_id == scope.scope_id and record.task_id == task.task_id
    # The approval payload carries the full draft plus the classification; the
    # gate serializes args to a JSON string, so parse it back.
    assert record.tools[0]["name"] == "draft_create_local"
    payload = json.loads(record.tools[0]["args"])
    assert payload["to"] == "landlord@example.com"
    assert payload["body"] == "We agree and will pay by the 15th."
    assert payload["category"] == "reply"
    assert seams["tasks"].get(task.task_id).status is TaskStatus.WAITING_APPROVAL
    assert DraftStore(tmp_path).list() == []

    resolved = resolve_pending(outcome.pending_id, "approve", settings, pending, graph=graph)

    assert resolved.outcome == "completed" and "Draft stored" in resolved.answer
    drafts = DraftStore(tmp_path).list()
    assert len(drafts) == 1 and drafts[0].status == "local_only"
    assert drafts[0].to == "landlord@example.com" and drafts[0].category == "reply"
    assert seams["tasks"].get(task.task_id).status is TaskStatus.COMPLETED

    correlation = scope.correlation_id
    chain = [
        (row["actor"], row["tool"], row["decision"])
        for row in read_audit(tmp_path)
        if row.get("correlation_id") == correlation
    ]
    assert ("inbound", "accept_inbound", "accepted") in chain
    assert ("console", "inbound.triage", "triggered") in chain
    # LangGraph re-runs the gate node on resume, so the gate's authorized row
    # appears twice (pre-interrupt, then the re-run); run_tools' execution-time
    # re-check adds the third. Every other row is once.
    assert chain.count(("gateway", "draft_create_local", "authorized")) == 3
    assert ("human", "draft_create_local", "approved") in chain
    assert ("loom", "draft_create_local", "executed") in chain


def test_triage_of_an_unauthenticated_message_skips_the_graph_run(tmp_path):
    settings, graph, seams = _setup(tmp_path, [])  # empty script: the model must never be reached
    task, scope = _accept(seams, "sim-002")
    assert scope is None
    pending = PendingStore(tmp_path)

    outcome = run_triage("sim-002", settings, pending, graph=graph)

    assert outcome.outcome == "completed"
    assert outcome.answer == "no scope was issued for this message; nothing to triage"
    assert pending.list_pending() == []
    assert DraftStore(tmp_path).list() == []
    assert seams["tasks"].get(task.task_id).status is TaskStatus.RUNNING  # untouched: nothing ran
    decisions = [(row["tool"], row["decision"]) for row in read_audit(tmp_path) if row["actor"] == "console"]
    assert ("inbound.triage", "triggered") in decisions and ("inbound.triage", "skipped") in decisions


def test_triage_reject_writes_no_draft_and_completes_the_task(tmp_path):
    settings, graph, seams = _setup(
        tmp_path,
        [tool_call_msg("draft_create_local", {"to": "landlord@example.com", "subject": "Heating bill", "body": "We agree.", "category": "reply"}, "c1")],
    )
    task, scope = _accept(seams, "sim-001")
    pending = PendingStore(tmp_path)

    outcome = run_triage("sim-001", settings, pending, graph=graph)
    resolved = resolve_pending(outcome.pending_id, "reject", settings, pending, graph=graph)

    assert resolved.outcome == "completed"
    assert DraftStore(tmp_path).list() == []
    assert seams["tasks"].get(task.task_id).status is TaskStatus.COMPLETED
    chain = [
        (row["actor"], row["decision"])
        for row in read_audit(tmp_path)
        if row.get("correlation_id") == scope.correlation_id
    ]
    assert ("human", "rejected") in chain
    assert ("loom", "executed") not in chain


def test_triage_of_an_unknown_message_is_a_valueerror(tmp_path):
    settings, _graph, _seams = _setup(tmp_path, [])

    with pytest.raises(ValueError, match="no such inbox message"):
        run_triage("nope", settings, PendingStore(tmp_path), graph=None)
