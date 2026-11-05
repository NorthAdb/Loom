"""The final-rework regressions: one production-path test per P0/P1 item of the audit.

Each test mirrors the auditor's probe (handoffs/loom-audit-probes.py) against
the real classes and the real graph, so the before/after evidence for every
item is a named test instead of a claim.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

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


def test_P0_2_the_model_facing_name_hits_the_registry_end_to_end(tmp_path):
    """The bound name the model sees IS the name the graph resolves; the audit records the canonical key."""
    import sys

    from loom.tools.mcp import McpServerStore, build_mcp_registry, register_server
    from loom.tools.mcp.bridge import canonical_action_name, model_action_name

    server = REPO / "fixtures" / "mcp" / "notes_server.py"
    store = McpServerStore(tmp_path)
    record = register_server(
        store,
        server_id="notes",
        name="Notes",
        command=[sys.executable, str(server)],
        allowed_tools=["notes_search"],
    )
    assert record.needs_review is False
    registry = build_mcp_registry(settings := Settings(
        loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox"
    ))
    canonical = "mcp:notes:notes_search"
    bound = model_action_name(canonical)
    assert registry[canonical].func.name == bound  # what bind_tools advertises
    assert canonical_action_name(bound, registry) == canonical  # the reverse hits the key

    scope = issue_scope("owner", "eval", "mcp", [canonical], ttl_seconds=None)
    ScopeStore(tmp_path).add(scope)
    graph = build_graph(
        ScriptedChatModel(
            messages=iter([
                tool_call_msg(bound, {"query": "heating"}, "c1"),
                AIMessage(content="Notes found."),
            ])
        ),
        settings,
        memory=FakeSemantic(),
        gateway=ToolGateway(AuditLog(tmp_path), ScopeStore(tmp_path)),
        extra_registry=registry,
    )
    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "search"}], "run_id": "n1", "scope_id": scope.scope_id},
        {"configurable": {"thread_id": "n1"}},
    )
    assert "__interrupt__" in paused  # the proposal reached the gate under its canonical name
    done = graph.invoke(Command(resume={"decision": "approve"}), {"configurable": {"thread_id": "n1"}})
    texts = [getattr(m, "content", "") for m in done["messages"]]
    assert any("LOOM-MCP-NOTES-MARKER" in str(text) for text in texts)
    rows = read_audit(tmp_path)
    assert any(row["tool"] == canonical and row["decision"] == "executed" for row in rows)


def test_P0_2_chat_gains_mcp_only_through_the_explicit_flag(monkeypatch, tmp_path, capsys):
    """The chat entrypoint runs MCP-free by default and merges registered tools only with --mcp."""
    import sys

    import loom.cli.main  # ensures sys.modules has the real module
    import loom.core.runtime
    from loom.tools.mcp import McpServerStore, register_server

    server = REPO / "fixtures" / "mcp" / "notes_server.py"
    register_server(
        McpServerStore(tmp_path),
        server_id="notes",
        name="Notes",
        command=[sys.executable, str(server)],
        allowed_tools=["notes_search"],
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))
    captured = {}

    class Recording:
        def __init__(self):
            self.bound_names = []

        def bind_tools(self, tools, **kwargs):
            self.bound_names = [getattr(tool, "name", None) for tool in tools]
            return self

        def invoke(self, messages):
            return AIMessage(content="hello there")

    recording = Recording()

    def fake_make_model(settings):
        return recording

    def fake_chat_session(graph, thread_id, read, emit, *, budget=None):
        captured["names"] = list(recording.bound_names)

    monkeypatch.setattr(loom.core.runtime, "make_model", fake_make_model)
    cli_module = sys.modules["loom.cli.main"]  # the package __init__ re-exports `main`, shadowing the module
    monkeypatch.setattr(cli_module, "chat_session", fake_chat_session)

    assert cli_module.main(["chat"]) == 0, capsys.readouterr().out
    assert not any(name and name.startswith("mcp_") for name in captured["names"])  # off by default
    assert cli_module.main(["chat", "--mcp"]) == 0, capsys.readouterr().out
    assert "mcp_notes_notes_search" in captured["names"]  # the flag merged the registered server


def test_P0_3_the_return_value_never_carries_a_known_credential_into_the_model_or_a_backup(tmp_path, monkeypatch):
    """Scrubbing happens before the text reaches the model, so checkpoints and backups hold no secret."""
    import sys
    import zipfile

    from loom.ops.backup import create_backup
    from loom.tools.mcp import McpServerStore, build_mcp_registry, register_server

    secret = "dummy-credential-value-9f2b"
    server = REPO / "fixtures" / "mcp" / "echo_server.py"
    store = McpServerStore(tmp_path)
    register_server(
        store,
        server_id="echo",
        name="Echo",
        command=[sys.executable, str(server)],
        allowed_tools=["reveal_token"],
        credential_env="LOOM_ECHO_TOKEN",
    )
    monkeypatch.setenv("LOOM_ECHO_TOKEN", secret)
    if True:
        settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
        registry = build_mcp_registry(settings)
        canonical = "mcp:echo:reveal_token"
        bound = registry[canonical].func.name
        scope = issue_scope("owner", "eval", "mcp", [canonical], ttl_seconds=None)
        ScopeStore(tmp_path).add(scope)
        graph = build_graph(
            ScriptedChatModel(messages=iter([
                tool_call_msg(bound, {}, "c1"),
                AIMessage(content="done"),
            ])),
            settings,
            memory=FakeSemantic(),
            gateway=ToolGateway(AuditLog(tmp_path), ScopeStore(tmp_path)),
            extra_registry=registry,
        )
        graph.invoke(
            {"messages": [{"role": "user", "content": "echo"}], "run_id": "e1", "scope_id": scope.scope_id},
            {"configurable": {"thread_id": "e1"}},
        )
        done = graph.invoke(Command(resume={"decision": "approve"}), {"configurable": {"thread_id": "e1"}})
        tool_texts = [str(m.content) for m in done["messages"] if getattr(m, "type", "") == "tool"]
        assert any("***" in text and secret not in text for text in tool_texts)

        archive = create_backup(tmp_path, tmp_path / "backups")
        blob = b"".join(
            zipfile.ZipFile(archive).read(name) for name in zipfile.ZipFile(archive).namelist()
        )
        assert secret.encode() not in blob  # the checkpoint WAL holds no credential


def test_P1_7_the_call_path_performs_the_mcp_handshake(tmp_path):
    """A spec-conforming server that refuses pre-handshake requests works through the bridge."""
    import sys

    from loom.tools.mcp import McpServerStore, build_mcp_registry, register_server

    server = REPO / "fixtures" / "mcp" / "strict_server.py"
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    register_server(
        McpServerStore(tmp_path),
        server_id="strict",
        name="Strict",
        command=[sys.executable, str(server)],
        allowed_tools=["ping"],
    )
    registry = build_mcp_registry(settings)
    spec = registry["mcp:strict:ping"]

    assert spec.func.invoke({}) == "pong"  # list_tools initialized first; the call completes
