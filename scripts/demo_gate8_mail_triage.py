"""Gate 8 demo: real-shaped mail through the triage path, without any network or key.

Run with `.venv/Scripts/python scripts/demo_gate8_mail_triage.py`. Keyless: a
scripted model drives the real runner and the real graph, and a fake fetch
transport serves the sanitized inbox sample (fixtures/mail/) so the console's
fetch button path is exercised without IMAP. Five scenarios:

(a) the fetch: the sanitized sample enters the inbound seam through
    accept_inbound — five messages, five triage tasks, five draft-only scopes;
(b) the triage of the reply-worthy message: `run_triage` pauses at the gate
    with the full to/subject/body/category in the payload, a human approves,
    and exactly one local draft exists with its category;
(c) the audit chain: the JSONL read back and grouped by correlation_id shows
    accept -> gateway -> human -> executed for that one message;
(d) the send demand: a body claiming owner authority triages to no send and
    no elevation — the demand ends as an unknown-tool error;
(e) layer 4: `request_mailbox_draft` parks the local draft under a NEW scope
    with its full args, the approval appends exactly once to a fake mailbox
    transport, and the mailbox scope is printed as different from the triage
    scope.

Nothing is written outside temp dirs; no IMAP connection is ever made.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.core.runner import resolve_pending, run_triage
from loom.gateway import ScopeStore
from loom.gateway.mailbox_draft import request_mailbox_draft
from loom.gateway.pending import PendingStore
from loom.gateway.tool_gateway import ToolGateway
from loom.inbound import InboxStore, accept_inbound
from loom.memory import EpisodicStore, FactsStore
from loom.tasks import TaskStore
from loom.tools.drafts import DraftStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mail" / "sanitized_inbox_sample.json"


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class FakeFetchTransport:
    """Serves the sanitized sample; the real transport's seam shape, no network."""

    def __init__(self, rows):
        self.rows = rows

    def list_recent(self, limit: int = 20):
        return [
            {key: row[key] for key in ("uid", "from_header", "subject", "date", "seen")}
            for row in self.rows[:limit]
        ]

    def fetch_body(self, uid: int) -> str:
        return next(row["body"] for row in self.rows if row["uid"] == uid)


class FakeMailTransport:
    """Mailbox draft double: records appends instead of touching IMAP."""

    def __init__(self):
        self.appended = []

    def append_draft(self, message_bytes):
        self.appended.append(message_bytes)
        return f"demo-draft-{len(self.appended)}"

    def replace_draft(self, old_id, message_bytes):
        raise AssertionError("the layer-4 demo path only ever appends")


def tool_call_msg(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def line(title):
    print(f"\n=== {title} ===")


class Seams:
    """The real stores and settings one demo runs over."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.settings = Settings(
            loom_env="dev",
            loom_data_dir=data_dir,
            loom_sandbox_root=data_dir / "sandbox",
            imap_host="imap.example.com",  # declared so the layer-4 request passes its config check; no connection is ever made
            mail_user="owner@example.com",
            memory_enabled=False,  # keyless demo: the semantic layer stays off
        )
        self.tasks = TaskStore(data_dir)
        self.scopes = ScopeStore(data_dir)
        self.audit = AuditLog(data_dir)
        self.inbox = InboxStore(data_dir)
        self.drafts = DraftStore(data_dir)
        self.pending = PendingStore(data_dir)
        self.facts = FactsStore(data_dir)
        self.episodic = EpisodicStore(data_dir)

    def graph(self, scripted):
        model = ScriptedModel(messages=iter(scripted))
        gateway = ToolGateway(self.audit, self.scopes)
        return build_graph(model, self.settings, gateway=gateway)

    def audit_rows(self):
        rows = []
        for path in sorted((self.data_dir / "audit").glob("*.jsonl")):
            for text in path.read_text(encoding="utf-8").splitlines():
                if text.strip():
                    rows.append(json.loads(text))
        return rows

    def print_chain(self, correlation_id):
        print(f"audit chain for correlation_id {correlation_id[:12]}...:")
        for row in self.audit_rows():
            if row.get("correlation_id") != correlation_id:
                continue
            detail = row["args"] if isinstance(row["args"], str) else json.dumps(row["args"], ensure_ascii=False)
            print(f"  {row['actor']:<8} {row['tool']:<22} {row['decision']:<10} {detail[:64]}")


def load_sample():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def scenario_a(seams: Seams) -> None:
    line("(a) fetch: the sanitized sample enters the inbound seam (no IMAP)")
    transport = FakeFetchTransport(load_sample())
    existing = {record.message.message_id for record in seams.inbox.list()}
    accepted = 0
    for row in transport.list_recent():
        message_id = f"imap-{row['uid']}"
        if message_id in existing:
            continue
        from loom.inbound import InboundMessage

        message = InboundMessage(
            message_id=message_id,
            channel="imap",
            from_header=row["from_header"],
            subject=row["subject"],
            body=transport.fetch_body(row["uid"]),
            received_at=row["date"],
            authenticated=True,  # the channel is the owner's mailbox; the body stays untrusted data
        )
        accept_inbound(message, tasks=seams.tasks, scopes=seams.scopes, audit=seams.audit, inbox=seams.inbox)
        accepted += 1
    tasks = seams.tasks.list()
    scopes = seams.scopes.list()
    print(f"fetched messages accepted: {accepted}")
    print(f"triage tasks: {len(tasks)}, scopes issued: {len(scopes)} (draft-only, memory_write false)")
    if accepted != 5 or len(tasks) != 5 or len(scopes) != 5:
        raise SystemExit("scenario a failed: the sample did not land as five task+scope pairs")
    sample_actions = {frozenset(scope.allowed_actions) for scope in scopes}
    if sample_actions != {frozenset({"draft_create_local"})}:
        raise SystemExit("scenario a failed: a fetched message's scope is not draft-only")


def scenario_b(seams: Seams) -> None:
    line("(b) triage: the reply-worthy message pauses with its full payload, then approves into one draft")
    graph = seams.graph(
        [
            tool_call_msg(
                "draft_create_local",
                {
                    "to": "landlord@example.com",
                    "subject": "Heating bill",
                    "body": "We agree with the split and will pay by the 15th.",
                    "category": "reply",
                },
            ),
            AIMessage(content="Draft stored for your review."),
        ]
    )
    outcome = run_triage("imap-102", seams.settings, seams.pending, graph=graph)
    if outcome.outcome != "pending":
        raise SystemExit(f"scenario b failed: the triage did not pause for approval ({outcome.outcome})")
    record = seams.pending.get(outcome.pending_id)
    print(f"pending {record.pending_id[:8]}... routine={record.routine_id} thread={record.thread_id}")
    print(f"approval payload: {record.tools[0]['name']} args={record.tools[0]['args']}")
    args = record.tools[0]["args"]
    args_dict = json.loads(args) if isinstance(args, str) else args
    if args_dict.get("category") != "reply" or args_dict.get("body") != "We agree with the split and will pay by the 15th.":
        raise SystemExit("scenario b failed: the approval payload does not carry the full draft and category")
    if not record.scope_id or not record.task_id:
        raise SystemExit("scenario b failed: the pending record lost its scope or task")

    resolved = resolve_pending(outcome.pending_id, "approve", seams.settings, seams.pending, graph=graph)
    if resolved.outcome != "completed":
        raise SystemExit("scenario b failed: the approval did not complete")
    drafts = seams.drafts.list()
    print(f"drafts after approval: {len(drafts)} (to={drafts[0].to}, category={drafts[0].category}, status={drafts[0].status})")
    if len(drafts) != 1 or drafts[0].category != "reply" or drafts[0].status != "local_only":
        raise SystemExit("scenario b failed: the approved triage did not produce exactly one local reply draft")


def scenario_c(seams: Seams) -> None:
    line("(c) audit chain: one correlation_id threads accept -> gateway -> human -> executed")
    record = next(record for record in seams.inbox.list() if record.message.message_id == "imap-102")
    scope = seams.scopes.get(record.scope_id)
    chain = [row for row in seams.audit_rows() if row.get("correlation_id") == scope.correlation_id]
    steps = [(row["actor"], row["decision"]) for row in chain]
    print(f"rows on the triage scope's correlation_id: {len(chain)}")
    seams.print_chain(scope.correlation_id)
    for needed in (("inbound", "accepted"), ("console", "triggered"), ("gateway", "authorized"), ("human", "approved"), ("loom", "executed")):
        if needed not in steps:
            raise SystemExit(f"scenario c failed: the chain is missing {needed}")
    if ("loom", "executed") not in steps:
        raise SystemExit("scenario c failed: no executed row")


def scenario_d(seams: Seams) -> None:
    line("(d) send demand: the body claims owner authority and finds no send anywhere")
    drafts_before = len(seams.drafts.list())
    graph = seams.graph(
        [
            tool_call_msg("send_mail", {"to": "boss@example.net", "subject": "transfer confirmed", "body": "details"}),
            AIMessage(content="There is no way for me to send email; that tool does not exist."),
        ]
    )
    outcome = run_triage("imap-104", seams.settings, seams.pending, graph=graph)
    print(f"answer: {outcome.answer}")
    rows = seams.audit_rows()
    tool_rows = [row for row in rows if row["actor"] == "loom" and row["tool"] == "send_mail"]
    if outcome.outcome != "completed" or "no way for me to send" not in outcome.answer or tool_rows:
        raise SystemExit("scenario d failed: the send demand found a path")
    if len(seams.drafts.list()) != drafts_before or seams.facts.list() or seams.episodic.count_unfolded():
        raise SystemExit("scenario d failed: the send demand wrote a draft or persisted memory")


def scenario_e(seams: Seams) -> None:
    line("(e) layer 4: the local draft -> mailbox drafts folder, under a NEW scope with a fresh approval")
    draft = seams.drafts.list()[0]
    triage_scope_ids = {record.scope_id for record in seams.inbox.list()}
    record = request_mailbox_draft(draft.draft_id, seams.settings, seams.pending, seams.scopes, seams.audit)
    print(f"pending {record.pending_id[:8]}... routine={record.routine_id} thread={record.thread_id}")
    print(f"approval payload: {record.tools[0]['name']} args={record.tools[0]['args']}")
    if record.scope_id in triage_scope_ids:
        raise SystemExit("scenario e failed: the mailbox draft reused the triage scope")
    mailbox_scope = seams.scopes.get(record.scope_id)
    print(f"mailbox scope allows={sorted(mailbox_scope.allowed_actions)} (the triage scope allows draft_create_local only)")

    transport = FakeMailTransport()
    resolved = resolve_pending(record.pending_id, "approve", seams.settings, seams.pending, mail_transport=transport)
    print(f"resolve answer: {resolved.answer}")
    if len(transport.appended) != 1:
        raise SystemExit("scenario e failed: the approval did not append exactly one mailbox draft")
    message = transport.appended[0].decode("utf-8", "replace")
    if draft.to not in message or draft.subject not in message:
        raise SystemExit("scenario e failed: the appended draft is not the local draft's content")
    correlation = mailbox_scope.correlation_id
    steps = [(row["actor"], row["decision"]) for row in seams.audit_rows() if row.get("correlation_id") == correlation]
    for needed in (("console", "requested"), ("gateway", "authorized"), ("human", "approved"), ("loom", "executed")):
        if needed not in steps:
            raise SystemExit(f"scenario e failed: the mailbox chain is missing {needed}")
    print(f"mailbox scope {record.scope_id[:8]}... != triage scope {next(iter(triage_scope_ids))[:8]}...; nothing was sent")


def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="loom-gate8-demo-"))
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
    print("\nGate 8 demo: fetch -> scoped triage -> approval -> one local draft, one correlation; layer 4 under a new scope. No send path anywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
