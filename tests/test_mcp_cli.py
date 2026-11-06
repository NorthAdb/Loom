"""The `loom mcp` CLI: register (probe, fingerprint, subset validation), list, revoke, review.

Each test drives the real `loom.cli.main` with the real fixture server; the
data dir is a tmp_path behind LOOM_DATA_DIR. The audit rows the CLI writes are
asserted alongside the store records.
"""

import json
import sys
from pathlib import Path

from conftest import read_audit

import loom.cli

REPO = Path(__file__).resolve().parents[1]
NOTES_SERVER = str(REPO / "fixtures" / "mcp" / "notes_server.py")
JSON_COMMAND = json.dumps([sys.executable, NOTES_SERVER])
SHELL_COMMAND = f'"{sys.executable}" "{NOTES_SERVER}"'


def _setup_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))


def _register(monkeypatch, tmp_path, *, command=JSON_COMMAND, force=False):
    argv = [
        "mcp", "register", "--id", "notes", "--name", "Canned notes",
        "--command", command, "--allow", "notes_search,notes_count",
        "--credential-env", "LOOM_NOTES_TOKEN",
    ]
    if force:
        argv.append("--force")
    return loom.cli.main(argv)


def _store(tmp_path):
    from loom.tools.mcp import McpServerStore

    return McpServerStore(tmp_path)


def test_register_probes_the_server_and_stores_the_fingerprint_with_an_audit_row(monkeypatch, tmp_path, capsys):
    _setup_env(monkeypatch, tmp_path)

    assert _register(monkeypatch, tmp_path) == 0

    record = _store(tmp_path).get("notes")
    assert record.transport == "stdio" and record.enabled is True and record.needs_review is False
    assert record.allowed_tools == {"notes_search", "notes_count"}
    assert record.credential_env == "LOOM_NOTES_TOKEN"  # the NAME; the value never exists here
    assert len(record.schema_fingerprint) == 64
    rows = read_audit(tmp_path)
    assert rows[-1]["actor"] == "mcp-cli" and rows[-1]["decision"] == "registered"
    assert json.loads(rows[-1]["args"])["fingerprint"] == record.schema_fingerprint[:12]
    out = capsys.readouterr().out
    assert "credential: env var LOOM_NOTES_TOKEN" in out
    assert "dummy-credential-value" not in out


def test_register_accepts_a_shell_ish_command_string(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)

    assert _register(monkeypatch, tmp_path, command=SHELL_COMMAND) == 0

    assert _store(tmp_path).get("notes").command == [sys.executable, NOTES_SERVER]


def test_duplicate_registration_refuses_and_force_freezes(monkeypatch, tmp_path, capsys):
    _setup_env(monkeypatch, tmp_path)
    _register(monkeypatch, tmp_path)
    first = _store(tmp_path).get("notes")

    assert _register(monkeypatch, tmp_path) == 1  # duplicate without --force
    assert "already registered" in capsys.readouterr().out
    assert _store(tmp_path).get("notes").schema_fingerprint == first.schema_fingerprint

    assert _register(monkeypatch, tmp_path, force=True) == 0
    refrozen = _store(tmp_path).get("notes")
    assert refrozen.needs_review is True
    decisions = [row["decision"] for row in read_audit(tmp_path) if row["actor"] == "mcp-cli"]
    assert decisions == ["registered", "reregistered"]


def test_register_refuses_an_allow_typo_naming_the_offered_tools(monkeypatch, tmp_path, capsys):
    _setup_env(monkeypatch, tmp_path)

    code = loom.cli.main([
        "mcp", "register", "--id", "notes", "--name", "Notes",
        "--command", JSON_COMMAND, "--allow", "notes_serch",
    ])

    assert code == 1
    out = capsys.readouterr().out
    assert "notes_serch" in out and "notes_search" in out  # the typo and the offered list
    assert _store(tmp_path).list() == []


def test_list_revoke_and_review_round_trip(monkeypatch, tmp_path, capsys):
    _setup_env(monkeypatch, tmp_path)
    _register(monkeypatch, tmp_path)

    assert loom.cli.main(["mcp", "list"]) == 0
    out = capsys.readouterr().out
    assert "notes" in out and "credential_env=LOOM_NOTES_TOKEN" in out and "needs_review=false" in out

    assert loom.cli.main(["mcp", "revoke", "notes"]) == 0
    assert _store(tmp_path).get("notes").enabled is False
    assert loom.cli.main(["mcp", "revoke", "ghost"]) == 1

    # A revoke freezes nothing; review re-probes a live server and refreshes the fingerprint.
    _store(tmp_path).set_status("notes", schema_fingerprint="0" * 64, needs_review=True, enabled=True)
    assert loom.cli.main(["mcp", "review", "notes"]) == 0
    record = _store(tmp_path).get("notes")
    assert record.needs_review is False and record.schema_fingerprint != "0" * 64
    decisions = [row["decision"] for row in read_audit(tmp_path) if row["actor"] == "mcp-cli"]
    assert decisions == ["registered", "revoked", "reviewed"]


def test_review_of_an_unknown_server_exits_1(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)

    assert loom.cli.main(["mcp", "review", "ghost"]) == 1
