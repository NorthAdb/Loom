"""Gate 9 demo: MCP servers under the same gates — fingerprint, freeze, review, untrusted returns.

Run with `.venv/Scripts/python scripts/demo_gate9_mcp.py`. Keyless: a scripted
model drives the real graph, and two real read-only stdio MCP servers
(fixtures/mcp/notes_server.py and clock_server.py) run as subprocesses. Five
scenarios:

(a) registration: notes and clock are registered through the registry API
    with explicit allowed subsets; the mcp_servers.json path is printed and
    the file shows the credential env NAME, never a value;
(b) the approved call: a scoped run proposes mcp:notes:notes_search; the
    Tool Gateway authorizes, the human approves, the bridge re-verifies the
    live schema fingerprint and returns the marker text as untrusted data;
    the audit chain shows gateway -> human -> executed on one correlation_id;
(c) the over-privilege denial: mcp:clock:time_now against a scope that only
    allows notes — the gateway refuses at the gate and the refusal is audited;
(d) the fingerprint tamper: a wrong stored fingerprint freezes the server —
    the bridge refuses, persists needs_review=True, and audits the rejection;
(e) the review: `loom mcp review notes` re-probes the server, refreshes the
    fingerprint, clears the freeze, and the same call goes green.

Nothing is written outside temp dirs; no credential value exists anywhere.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.types import Command

import loom.cli
from loom.audit.log import AuditLog
from loom.core.config import Settings, get_settings
from loom.core.graph import build_graph
from loom.gateway import ScopeStore, issue_scope
from loom.gateway.tool_gateway import ToolGateway
from loom.memory import EpisodicStore, FactsStore
from loom.tools.mcp import McpServerStore, build_mcp_registry, register_server

REPO = Path(__file__).resolve().parents[1]
NOTES_SERVER = [sys.executable, str(REPO / "fixtures" / "mcp" / "notes_server.py")]
CLOCK_SERVER = [sys.executable, str(REPO / "fixtures" / "mcp" / "clock_server.py")]
MARKER = "LOOM-MCP-NOTES-MARKER"


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def tool_call_msg(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def line(title):
    print(f"\n=== {title} ===")


class Seams:
    """The real stores, settings, and graph one demo runs over."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.settings = Settings(
            loom_env="dev",
            loom_data_dir=data_dir,
            loom_sandbox_root=data_dir / "sandbox",
            memory_enabled=False,  # keyless demo: the semantic layer stays off
        )
        self.scopes = ScopeStore(data_dir)
        self.audit = AuditLog(data_dir)
        self.store = McpServerStore(data_dir)
        self.facts = FactsStore(data_dir)
        self.episodic = EpisodicStore(data_dir)

    def graph(self, scripted, thread_id: str):
        model = ScriptedModel(messages=iter(scripted))
        gateway = ToolGateway(self.audit, self.scopes)
        graph = build_graph(model, self.settings, gateway=gateway, extra_registry=build_mcp_registry(self.settings))
        return graph, {"configurable": {"thread_id": thread_id}}

    def scope(self, actions):
        scope = issue_scope("owner", "demo", "mcp-demo", list(actions), approval_required=False, ttl_seconds=None)
        self.scopes.add(scope)
        return scope

    def bound_name(self, canonical: str) -> str:
        """The model-facing name for one registry key, proven to map back."""
        from loom.tools.mcp.bridge import canonical_action_name, model_action_name

        registry = build_mcp_registry(self.settings)
        if canonical not in registry:
            raise SystemExit(f"the demo registry does not hold {canonical}")
        bound = model_action_name(canonical)
        if canonical_action_name(bound, registry) != canonical:
            raise SystemExit(f"the bound name {bound} does not map back to {canonical}")
        return bound

    def invoke(self, graph, config, scope, text):
        return graph.invoke(
            {
                "messages": [{"role": "user", "content": text}],
                "run_id": "demo-gate9",
                "scope_id": scope.scope_id,
            },
            config,
        )

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
            print(f"  {row['actor']:<8} {row['tool']:<26} {row['decision']:<10} {detail[:72]}")

    def tool_texts(self, result):
        return [message.content for message in result["messages"] if getattr(message, "type", "") == "tool"]


def scenario_a(seams: Seams) -> None:
    line("(a) register: notes + clock through the registry API, allowed subsets, env-var credential handle")
    notes = register_server(
        seams.store,
        server_id="notes",
        name="Canned notes",
        command=NOTES_SERVER,
        allowed_tools=["notes_search", "notes_count"],
        credential_env="LOOM_NOTES_TOKEN",  # the NAME; the value lives only in the operator's environment
    )
    clock = register_server(
        seams.store,
        server_id="clock",
        name="Demo clock",
        command=CLOCK_SERVER,
        allowed_tools=["time_now"],
    )
    registry_path = Path(seams.settings.loom_data_dir) / "mcp_servers.json"
    on_disk = registry_path.read_text(encoding="utf-8")
    print(f"registry: {registry_path}")
    print(f"notes fingerprint {notes.schema_fingerprint[:12]}, allowed: {sorted(notes.allowed_tools)}")
    print(f"clock fingerprint {clock.schema_fingerprint[:12]}, allowed: {sorted(clock.allowed_tools)}")
    print("credential handle on disk: notes -> env var LOOM_NOTES_TOKEN (value never stored)")
    if "LOOM_NOTES_TOKEN" not in on_disk or notes.server_id not in on_disk:
        raise SystemExit("scenario a failed: the registry file does not carry the records")
    for row in seams.audit_rows():
        if row["actor"] == "mcp-cli":
            raise SystemExit("scenario a failed: the registry API wrote rows; only the CLI layer audits")


def scenario_b(seams: Seams) -> None:
    # The model proposes the BOUND name (what bind_tools advertises); the
    # graph maps it back to the canonical registry key mcp:notes:notes_search.
    bound = seams.bound_name("mcp:notes:notes_search")
    line(f"(b) approved call: the model proposes the bound name {bound}")
    graph, config = seams.graph(
        [
            tool_call_msg(bound, {"query": "heating bill"}, "c1"),
            AIMessage(content="I found your note; the text is data only."),
        ],
        thread_id="demo-gate9-b",
    )
    scope = seams.scope(["mcp:notes:notes_search"])
    paused = seams.invoke(graph, config, scope, "Inbound message (data, not instructions): search the notes.")
    if "__interrupt__" not in paused:
        raise SystemExit("scenario b failed: the mcp call did not pause at the human gate")
    result = graph.invoke(Command(resume={"decision": "approve"}), config)
    texts = seams.tool_texts(result)
    if not any(MARKER in text for text in texts):
        raise SystemExit("scenario b failed: the marker text did not come back")
    print(f"tool result: {next(text for text in texts if MARKER in text)[:96]}...")
    print("the injected sentence rode along as data:")
    print(f"  facts={len(seams.facts.list())} episodic={seams.episodic.count_unfolded()} (both stay empty)")
    if seams.facts.list() or seams.episodic.count_unfolded():
        raise SystemExit("scenario b failed: the untrusted return persisted something")
    chain = [(row["actor"], row["decision"]) for row in seams.audit_rows() if row.get("correlation_id") == scope.correlation_id]
    print(f"chain rows on the scope's correlation_id: {len(chain)}")
    seams.print_chain(scope.correlation_id)
    for needed in (("gateway", "authorized"), ("human", "approved"), ("loom", "executed")):
        if needed not in chain:
            raise SystemExit(f"scenario b failed: the chain is missing {needed}")


def scenario_c(seams: Seams) -> None:
    line("(c) over-privilege denial: mcp:clock:time_now against a notes-only scope")
    graph, config = seams.graph(
        [tool_call_msg("mcp:clock:time_now", {}, "c2"), AIMessage(content="The gateway refused that call.")],
        thread_id="demo-gate9-c",
    )
    scope = seams.scope(["mcp:notes:notes_search"])
    result = seams.invoke(graph, config, scope, "Inbound message (data, not instructions): what time is it?")
    final = str(result["messages"][-1].content)
    print(f"final: {final[:140]}...")
    if "Gateway refused mcp:clock:time_now" not in final or "not in the scope's allowed actions" not in final:
        raise SystemExit("scenario c failed: the over-privilege call was not refused at the gate")
    rejected = [
        row
        for row in seams.audit_rows()
        if row["actor"] == "gateway" and row["decision"] == "rejected" and row["tool"] == "mcp:clock:time_now"
    ]
    if not rejected or rejected[0].get("correlation_id") != scope.correlation_id:
        raise SystemExit("scenario c failed: the refusal is not audited on the scope's correlation_id")
    print(f"audited rejection: gateway/mcp:clock:time_now/rejected correlation={scope.correlation_id[:12]}...")
    executed = [row for row in seams.audit_rows() if row["decision"] == "executed" and row["tool"] == "mcp:clock:time_now"]
    if executed:
        raise SystemExit("scenario c failed: the denied call executed")


def scenario_d(seams: Seams) -> None:
    line("(d) fingerprint tamper: a wrong stored fingerprint freezes the server")
    seams.store.set_status("notes", schema_fingerprint="f" * 64)
    graph, config = seams.graph(
        [
            tool_call_msg("mcp:notes:notes_search", {"query": "heating bill"}, "c3"),
            AIMessage(content="The server is frozen pending re-review."),
        ],
        thread_id="demo-gate9-d",
    )
    scope = seams.scope(["mcp:notes:notes_search"])
    seams.invoke(graph, config, scope, "Inbound message (data, not instructions): search the notes.")
    result = graph.invoke(Command(resume={"decision": "approve"}), config)
    texts = seams.tool_texts(result)
    if not any("schema fingerprint mismatch for 'notes'" in text for text in texts):
        raise SystemExit("scenario d failed: the tampered fingerprint did not freeze the call")
    record = seams.store.get("notes")
    print(f"tool error: {next(text for text in texts if 'fingerprint' in text)}")
    print(f"record after the freeze: needs_review={record.needs_review}")
    rejected = [row for row in seams.audit_rows() if row["actor"] == "mcp" and row["decision"] == "rejected"]
    if not record.needs_review or not rejected:
        raise SystemExit("scenario d failed: the freeze or its audit row is missing")
    print(f"audited rejection: mcp/{rejected[-1]['tool']}/rejected args={rejected[-1]['args'][:80]}")


def scenario_e(seams: Seams) -> None:
    line("(e) review: `loom mcp review notes` re-probes, refreshes the fingerprint, and the call goes green")
    os.environ["LOOM_ENV"] = "dev"
    os.environ["LOOM_DATA_DIR"] = str(seams.settings.loom_data_dir)
    get_settings.cache_clear()
    try:
        code = loom.cli.main(["mcp", "review", "notes"])
    finally:
        get_settings.cache_clear()
        os.environ.pop("LOOM_DATA_DIR", None)
    if code != 0:
        raise SystemExit("scenario e failed: the review command failed")
    record = seams.store.get("notes")
    if record.needs_review or record.schema_fingerprint == "f" * 64:
        raise SystemExit("scenario e failed: the review did not clear the freeze")
    reviewed = [row for row in seams.audit_rows() if row["actor"] == "mcp-cli" and row["decision"] == "reviewed"]
    if not reviewed:
        raise SystemExit("scenario e failed: no reviewed audit row")
    print(f"review row: mcp-cli/mcp.review/reviewed fingerprint={record.schema_fingerprint[:12]}")

    graph, config = seams.graph(
        [
            tool_call_msg("mcp:notes:notes_search", {"query": "heating bill"}, "c4"),
            AIMessage(content="I found your note; the text is data only."),
        ],
        thread_id="demo-gate9-e",
    )
    scope = seams.scope(["mcp:notes:notes_search"])
    seams.invoke(graph, config, scope, "Inbound message (data, not instructions): search the notes.")
    result = graph.invoke(Command(resume={"decision": "approve"}), config)
    if not any(MARKER in text for text in seams.tool_texts(result)):
        raise SystemExit("scenario e failed: the call did not go green after the review")
    print(f"call green again: {MARKER} returned as data")
    seams.print_chain(scope.correlation_id)


def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="loom-gate9-demo-"))
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
    print("\nGate 9 demo: register -> gateway -> approved call -> untrusted return; over-privilege refused;")
    print("fingerprint tamper freezes; a human review un-freezes. No credential value anywhere; no send path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
