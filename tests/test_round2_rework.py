"""The second-round rework regressions: one production-path test per item of
AUDIT-round2 (F-R1, F-R3, F-R2, and the two stale doc sentences).

Each test mirrors the round-2 auditor's probe against the real classes and
the real graph, so a failure that the probe found by hand is now a named,
repeatable test: the mail password and the LLM key joining the MCP scrub
set, a short credential value not rewriting ordinary text, the Audit view
rendering the args cell its header promises, and an empty IMAP FETCH
segment returning an empty body instead of raising.
"""

import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

from conftest import ScriptedChatModel, read_audit, tool_call_msg
from langchain_core.messages import AIMessage
from langgraph.types import Command

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.core.secrets import known_secret_values, scrub_text
from loom.gateway import ScopeStore, issue_scope
from loom.gateway.tool_gateway import ToolGateway
from loom.ops.backup import create_backup
from loom.tools.mail_fetch import ImapFetchTransport
from loom.tools.mcp import McpServerStore, build_mcp_registry, register_server
from loom.tools.mcp.bridge import _scrub

MAIL_SECRET = "PROBE-FAKE-MAIL-PASSWORD-r2"
LLM_SECRET = "PROBE-FAKE-LLM-KEY-r2"
NOTES_SECRET = "PROBE-FAKE-NOTES-TOKEN-r2"
OTHER_SECRET = "PROBE-FAKE-OTHER-TOKEN-r2"


class FakeSemantic:
    def __init__(self):
        self.added = []

    def add(self, texts):
        self.added.extend(texts)

    def search(self, query, limit=3):
        return []


def _settings(tmp_path, **overrides):
    values = {"mail_password": MAIL_SECRET, "llm_api_key": LLM_SECRET}
    values.update(overrides)
    return Settings(
        loom_env="dev",
        loom_data_dir=tmp_path,
        loom_sandbox_root=tmp_path / "sandbox",
        **values,
    )


def _register_echo(tmp_path, server_id="notes", credential_env="LOOM_NOTES_TOKEN"):
    server = REPO / "fixtures" / "mcp" / "echo_server.py"
    return register_server(
        McpServerStore(tmp_path),
        server_id=server_id,
        name=server_id,
        command=[sys.executable, str(server)],
        allowed_tools=["echo"],
        credential_env=credential_env,
    )


def _run_echo(tmp_path, settings, *, text, canonical="mcp:notes:echo"):
    """Drive one approved MCP echo call through the real graph; return (paused, done)."""
    registry = build_mcp_registry(settings)
    bound = registry[canonical].func.name
    scope = issue_scope("owner", "demo", "mcp", [canonical], ttl_seconds=None)
    ScopeStore(tmp_path).add(scope)
    graph = build_graph(
        ScriptedChatModel(messages=iter([tool_call_msg(bound, {"text": text}), AIMessage(content="done")])),
        settings,
        memory=FakeSemantic(),
        gateway=ToolGateway(AuditLog(tmp_path, settings=settings), ScopeStore(tmp_path)),
        extra_registry=registry,
    )
    config = {"configurable": {"thread_id": "r2"}}
    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "echo"}], "run_id": "r2", "scope_id": scope.scope_id},
        config,
    )
    done = graph.invoke(Command(resume={"decision": "approve"}), config)
    return paused, done


def test_R2_1_the_scrub_set_covers_the_mail_password_and_the_llm_key(tmp_path):
    """_scrub masks Settings secrets, not only registered server credentials."""
    text = f"mail={MAIL_SECRET} llm={LLM_SECRET} heating bill due"

    scrubbed = _scrub(text, _settings(tmp_path))

    assert MAIL_SECRET not in scrubbed and LLM_SECRET not in scrubbed
    assert scrubbed.count("***") == 2
    assert "heating bill due" in scrubbed  # the surrounding text is untouched


def test_R2_1_an_echo_of_every_known_secret_reaches_no_persisted_surface(tmp_path, monkeypatch):
    """A tool echoing the mail password, the LLM key, and server tokens leaves
    none of them in the model-visible text, the audit, the approval payload,
    or a backup (checkpoint WAL included)."""
    monkeypatch.setenv("LOOM_NOTES_TOKEN", NOTES_SECRET)
    monkeypatch.setenv("PROBE_OTHER_TOKEN", OTHER_SECRET)
    _register_echo(tmp_path)
    _register_echo(tmp_path, server_id="other", credential_env="PROBE_OTHER_TOKEN")
    settings = _settings(tmp_path)
    text = f"heating bill {MAIL_SECRET} {LLM_SECRET} {NOTES_SECRET} {OTHER_SECRET}"

    paused, done = _run_echo(tmp_path, settings, text=text)

    tool_texts = [str(m.content) for m in done["messages"] if getattr(m, "type", "") == "tool"]
    assert tool_texts, "the echo call produced no tool message"
    blob = "\n".join(tool_texts)
    for secret in (MAIL_SECRET, LLM_SECRET, NOTES_SECRET, OTHER_SECRET):
        assert secret not in blob
    assert "heating bill" in blob and "***" in blob

    shown = paused["__interrupt__"][0].value["tools"][0]["args"]
    for secret in (MAIL_SECRET, LLM_SECRET, NOTES_SECRET, OTHER_SECRET):
        assert secret not in shown
    assert "heating bill" in shown  # the approval still shows the full proposal text

    audit_text = "\n".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "audit").glob("*.jsonl")
    )
    for secret in (MAIL_SECRET, LLM_SECRET, NOTES_SECRET, OTHER_SECRET):
        assert secret not in audit_text

    archive = create_backup(tmp_path, tmp_path / "backups")
    raw = b"".join(zipfile.ZipFile(archive).read(name) for name in zipfile.ZipFile(archive).namelist())
    for secret in (MAIL_SECRET, LLM_SECRET, NOTES_SECRET, OTHER_SECRET):
        assert secret.encode() not in raw


def test_R2_1_audit_args_mask_a_known_secret_value_under_any_key(tmp_path):
    """The audit persistence boundary masks known values even when the key
    name does not look secret-bearing; the key itself stays visible."""
    log = AuditLog(tmp_path, settings=_settings(tmp_path))

    log.append(run_id="r", actor="loom", tool="mcp:notes:echo", args={"q": f"x {MAIL_SECRET} y"}, decision="executed")

    rows = read_audit(tmp_path)
    assert MAIL_SECRET not in rows[0]["args"]
    assert '"q"' in rows[0]["args"] and "***" in rows[0]["args"]


LONG_SECRET = "PROBE-LONG-KEY-" + "K" * 300  # 315 chars: longer than the audit summary cap


def test_R2_1_audit_args_scrub_before_the_summary_cap_hides_a_long_secret(tmp_path):
    """A known secret longer than the 200-char summary cap must not leak its
    first 200 characters: the persistence scrub runs on the raw args before
    `summarize_args` truncates, not on the truncated summary."""
    import json

    log = AuditLog(tmp_path, settings=_settings(tmp_path, mail_password=LONG_SECRET))
    log.append(
        run_id="r",
        actor="loom",
        tool="t",
        args={"q": LONG_SECRET, "note": "ordinary visible text", "blob": "n" * 250},
        decision="executed",
    )

    rows = read_audit(tmp_path)
    args = rows[0]["args"]
    assert LONG_SECRET not in args
    assert LONG_SECRET[:200] not in args  # the prefix the old order wrote verbatim
    assert '"q"' in args and "***" in args
    assert "ordinary visible text" in args  # normal args still shown
    assert json.loads(args)["blob"] == "n" * 200  # the summary cap itself is unchanged

    short_dir = tmp_path / "short"
    short_log = AuditLog(short_dir, settings=_settings(short_dir, mail_password="bill", llm_api_key=""))
    short_log.append(run_id="r", actor="loom", tool="t", args={"note": "heating bill due"}, decision="executed")
    assert "heating bill due" in read_audit(short_dir)[0]["args"]


def test_R2_1_audit_args_scrub_a_secret_straddling_the_summary_cap(tmp_path):
    """A secret that starts before the cap and ends after it: the old order
    wrote its first (cap - offset) characters; scrubbing first leaves none."""
    value = "v" * 150 + LONG_SECRET  # the secret crosses the 200-char boundary

    log = AuditLog(tmp_path, settings=_settings(tmp_path, mail_password=LONG_SECRET))
    log.append(run_id="r", actor="loom", tool="t", args={"q": value}, decision="executed")

    args = read_audit(tmp_path)[0]["args"]
    assert LONG_SECRET not in args
    assert LONG_SECRET[:50] not in args  # the fragment inside the old summary's first 200 chars
    assert "v" * 150 in args and "***" in args  # non-secret prefix and mask still shown


def test_R2_1_a_short_credential_value_never_rewrites_ordinary_text(tmp_path, monkeypatch):
    """A credential whose value is the word `bill` must not turn a successful
    `heating bill due` echo into `heating *** due`."""
    monkeypatch.setenv("LOOM_SHORT_TOKEN", "bill")
    _register_echo(tmp_path, credential_env="LOOM_SHORT_TOKEN")
    settings = _settings(tmp_path, mail_password="", llm_api_key="")

    _paused, done = _run_echo(tmp_path, settings, text="heating bill due")

    tool_texts = [str(m.content) for m in done["messages"] if getattr(m, "type", "") == "tool"]
    assert tool_texts == ["heating bill due"]
    rows = read_audit(tmp_path)
    assert any(row["decision"] == "executed" and row["tool"] == "mcp:notes:echo" for row in rows)
    # The unit-level rule behind it: short values are not substring-scrubbed.
    assert scrub_text("heating bill due", ["bill"]) == "heating bill due"
    assert "bill" not in known_secret_values(settings)


def test_R2_3_the_audit_view_renders_the_args_cell_beside_a_separate_correlation_link(tmp_path):
    """Six headers, six cells: the args string is in its own cell and the
    correlation id stays a link in the next one."""
    from fastapi.testclient import TestClient

    import loom.console.app as console_app

    AuditLog(tmp_path).append(
        run_id="r",
        actor="gateway",
        tool="draft_create_local",
        args={"body": "UNIQUE_AUDIT_ARGS_BODY_R2"},
        decision="authorized",
        correlation_id="corr-r2",
    )
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    client = TestClient(console_app.create_app(tmp_path, settings, port=8688), base_url="http://127.0.0.1:8688")

    page = client.get("/audit", params={"correlation_id": "corr-r2"})

    assert page.status_code == 200
    assert "UNIQUE_AUDIT_ARGS_BODY_R2" in page.text
    assert page.text.count("<th>") == 6
    assert page.text.count("<td>") == 6
    args_cell = page.text.split("UNIQUE_AUDIT_ARGS_BODY_R2")[0].rsplit("<td>", 1)[-1]
    assert "corr-r2" not in args_cell  # the args cell is not the correlation link
    assert '<a href="/audit?correlation_id=corr-r2">corr-r2</a>' in page.text


def test_R2_2_an_empty_fetch_segment_returns_an_empty_body(tmp_path):
    """A FETCH answer with no body section is "" , not an IndexError."""

    class EmptyConnection:
        def select(self, mailbox, readonly=False):
            return "OK", [b"0"]

        def uid(self, command, *args):
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    transport = ImapFetchTransport(settings, connection_factory=EmptyConnection)

    assert transport.fetch_body(1) == ""


def test_R2_4_the_docs_no_longer_describe_the_old_fetch_or_unbuilt_gates():
    status = (REPO / "docs" / "STATUS.md").read_text(encoding="utf-8")
    docs_index = (REPO / "docs" / "README.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")

    assert "UID SEARCH RECENT" not in status
    assert "UID SEARCH ALL" in status
    assert "not built" not in docs_index
    assert "unbuilt" not in readme
