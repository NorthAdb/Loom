"""Gate 7 demo: the simulated inbound closed loop over the real gateway.

Run with `.venv/Scripts/python scripts/demo_gate7_inbound.py`. Keyless: a
scripted model drives the real graph with the real Tool Gateway, scope store,
task store, inbox, and draft store in a temp data dir. Five scenarios:

(a) the flagship loop: an authenticated owner fixture is accepted (task +
    draft-only scope), the triage turn proposes `draft_create_local`, the
    gate pauses, a human approves, and exactly one local draft exists with
    the whole audit chain threaded by the scope's correlation_id;
(b) the forged owner: an unauthenticated body claiming ownership gets no
    scope and the gateway refuses every tool call;
(c) the hidden instruction: a body that hides an operator note writes
    nothing into facts, semantic memory, or the episodic log;
(d) the expired scope: an authorization past its expires_at cannot execute
    even though the action set would allow it;
(e) the send demand: there is no send tool and no send route — the demand
    ends as an unknown-tool error.

Nothing is written outside temp dirs.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from loom.config import Settings

from loom.audit.log import AuditLog
from loom.core.graph import build_graph
from loom.gateway import ScopeStore, issue_scope
from loom.gateway.tool_gateway import ToolGateway
from loom.inbound import InboxStore, accept_inbound, compose_triage_prompt
from loom.inbound.sim_mail import load_sim_mail
from loom.memory import EpisodicStore, FactsStore
from loom.tasks import TaskStore
from loom.tools.drafts import DraftStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "inbound"


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def line(title):
    print(f"\n=== {title} ===")


def settings_for(data_dir: Path) -> Settings:
    return Settings(
        loom_env="dev",
        loom_data_dir=data_dir,
        loom_sandbox_root=data_dir / "sandbox",
        memory_enabled=False,  # keyless demo: the semantic layer stays off
    )


class Seams:
    """The real stores one closed loop runs over."""

    def __init__(self, data_dir: Path) -> None:
        self.settings = settings_for(data_dir)
        self.tasks = TaskStore(data_dir)
        self.scopes = ScopeStore(data_dir)
        self.audit = AuditLog(data_dir)
        self.inbox = InboxStore(data_dir)
        self.drafts = DraftStore(data_dir)
        self.facts = FactsStore(data_dir)
        self.episodic = EpisodicStore(data_dir)

    def accept(self, message):
        return accept_inbound(
            message, tasks=self.tasks, scopes=self.scopes, audit=self.audit, inbox=self.inbox
        )

    def graph(self, scripted, semantic=None):
        model = ScriptedModel(messages=iter(scripted))
        gateway = ToolGateway(self.audit, self.scopes)
        return build_graph(model, self.settings, memory=semantic, gateway=gateway)

    def audit_rows(self, correlation_id=None):
        rows = []
        for path in sorted((self.settings.loom_data_dir / "audit").glob("*.jsonl")):
            for text in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(text)
                if correlation_id is None or row.get("correlation_id") == correlation_id:
                    rows.append(row)
        return rows

    def print_chain(self, correlation_id):
        print(f"audit chain for correlation_id {correlation_id[:12]}...:")
        for row in self.audit_rows(correlation_id):
            marker = f" corr={row.get('correlation_id', '-')[:12]}..." if row.get("correlation_id") else ""
            detail = row["args"] if isinstance(row["args"], str) else json.dumps(row["args"], ensure_ascii=False)
            print(f"  {row['actor']:<8} {row['tool']:<20} {row['decision']:<10} {detail[:60]}{marker}")


def fixture(message_id):
    return next(message for message in load_sim_mail(FIXTURES) if message.message_id == message_id)


def scenario_a(seams: Seams) -> None:
    line("(a) owner mail: accept -> scoped triage -> approval -> one local draft")
    message = fixture("sim-001")
    task, scope = seams.accept(message)
    print(f"accepted: task={task.task_id[:8]} scope allows={sorted(scope.allowed_actions)} memory_write={scope.memory_write}")
    print(f"triage prompt frames the body as data: {compose_triage_prompt(message).splitlines()[0][:70]}...")

    from langgraph.types import Command

    graph = seams.graph(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "draft_create_local",
                        "args": {
                            "to": "landlord@example.com",
                            "subject": "Heating bill",
                            "body": "We agree with the split and will pay by the 15th.",
                        },
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Draft stored for your review."),
        ]
    )
    config = {"configurable": {"thread_id": "t1"}}
    invoke_in = {
        "messages": [{"role": "user", "content": compose_triage_prompt(message)}],
        "run_id": "run-a",
        "scope_id": scope.scope_id,
    }
    paused = graph.invoke(invoke_in, config)
    if "__interrupt__" not in paused:
        raise SystemExit("scenario a failed: the gate did not pause")
    print("gate paused for the human: draft_create_local awaits approval")
    graph.invoke(Command(resume={"decision": "approve"}), config)

    drafts = seams.drafts.list()
    print(f"drafts: {len(drafts)} (status={drafts[0].status}, to={drafts[0].to})")
    if len(drafts) != 1 or drafts[0].status != "local_only":
        raise SystemExit("scenario a failed: the draft is not a single local-only record")
    seams.print_chain(scope.correlation_id)


def scenario_b(seams: Seams) -> None:
    line("(b) forged owner: no scope, every tool call refused")
    message = fixture("sim-002")
    drafts_before = len(seams.drafts.list())
    task, scope = seams.accept(message)
    print(f"accepted: task={task.task_id[:8]} scope={scope} (an unauthenticated body cannot elevate itself)")

    graph = seams.graph(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "draft_create_local",
                        "args": {"to": "boss@example.net", "subject": "transfer", "body": "money"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    result = graph.invoke(
        {
            "messages": [{"role": "user", "content": compose_triage_prompt(message)}],
            "run_id": "run-b",
            "scope_id": None,
        },
        {"configurable": {"thread_id": "t2"}},
    )
    print(f"answer: {result['messages'][-1].content}")
    if len(seams.drafts.list()) != drafts_before:
        raise SystemExit("scenario b failed: a draft was written for a forged owner")


def scenario_c(seams: Seams) -> None:
    line("(c) hidden instruction: nothing persists into any memory")

    class QuietSemantic:
        added: ClassVar[list[str]] = []

        def add(self, texts):
            self.added.extend(texts)

        def search(self, query, limit=3):
            return []

    semantic = QuietSemantic()
    message = fixture("sim-003")
    _task, scope = seams.accept(message)
    graph = seams.graph([AIMessage(content="Understood, I have saved that preference permanently.")], semantic=semantic)
    graph.invoke(
        {
            "messages": [{"role": "user", "content": compose_triage_prompt(message)}],
            "run_id": "run-c",
            "scope_id": scope.scope_id,
        },
        {"configurable": {"thread_id": "t3"}},
    )
    print(f"model claimed: saved the preference; semantic writes: {len(semantic.added)}")
    print(f"facts rows: {len(seams.facts.list())}, unfolded episodic: {seams.episodic.count_unfolded()}")
    if semantic.added or seams.facts.list() or seams.episodic.count_unfolded():
        raise SystemExit("scenario c failed: untrusted content persisted")


def scenario_d(seams: Seams) -> None:
    line("(d) expired scope: right action set, wrong moment, refused")
    message = fixture("sim-004")
    _task, _live = seams.accept(message)
    expired = issue_scope(
        "owner",
        "sim_mail",
        f"inbound/sim_mail/{message.message_id}",
        ["draft_create_local"],
        expires_at="2020-01-01T00:00:00+08:00",
        ttl_seconds=None,
    )
    seams.scopes.add(expired)
    drafts_before = len(seams.drafts.list())
    graph = seams.graph(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "draft_create_local",
                        "args": {"to": "ops@team.example", "subject": "RSVP", "body": "Attending."},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    result = graph.invoke(
        {
            "messages": [{"role": "user", "content": compose_triage_prompt(message)}],
            "run_id": "run-d",
            "scope_id": expired.scope_id,
        },
        {"configurable": {"thread_id": "t4"}},
    )
    print(f"answer: {result['messages'][-1].content}")
    if len(seams.drafts.list()) != drafts_before:
        raise SystemExit("scenario d failed: an expired scope executed")


def scenario_e(seams: Seams) -> None:
    line("(e) send demand: no send tool exists to reach")
    message = fixture("sim-002")
    graph = seams.graph(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "send_mail",
                        "args": {"to": "boss@example.net", "subject": "transfer", "body": "money"},
                        "id": "call_1",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="There is no way for me to send email."),
        ]
    )
    result = graph.invoke(
        {
            "messages": [{"role": "user", "content": compose_triage_prompt(message)}],
            "run_id": "run-e",
            "scope_id": None,
        },
        {"configurable": {"thread_id": "t5"}},
    )
    tool_texts = [str(m.content) for m in result["messages"] if getattr(m, "type", "") == "tool"]
    print(f"tool result: {next(t for t in tool_texts if 'send_mail' in t)}")
    if not any("unknown tool send_mail" in text for text in tool_texts):
        raise SystemExit("scenario e failed: the send demand found a path")


def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="loom-gate7-demo-"))
    print(f"data dir: {data_dir}")
    seams = Seams(data_dir)
    try:
        scenario_a(seams)
        scenario_b(seams)
        scenario_c(seams)
        scenario_d(seams)
        scenario_e(seams)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    print("\nGate 7 demo: the loop holds - simulated inbound, scope, gateway, approval, local draft, correlation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
