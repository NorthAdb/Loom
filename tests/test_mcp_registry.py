"""Fingerprint vectors and the mcp_servers.json store: registration, freezing, mutation paths."""

import json

import pytest

from loom.ops.backup import BACKUP_ROOTS
from loom.tools.mcp import McpError, McpServerRecord, McpServerStore, schema_fingerprint

TOOL_SEARCH = {
    "name": "notes_search",
    "description": "Search the canned notes.",
    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}
TOOL_COUNT = {
    "name": "notes_count",
    "description": "Count the canned notes.",
    "inputSchema": {"type": "object", "properties": {}},
}


def _record(server_id="notes", **overrides):
    fields = {
        "server_id": server_id,
        "name": "Canned notes",
        "command": ["python", "notes_server.py"],
        "transport": "stdio",
        "schema_fingerprint": "a" * 64,
        "allowed_tools": frozenset({"notes_search"}),
        "credential_env": "LOOM_NOTES_TOKEN",
        "reviewed_at": "2026-11-06T09:00:00+08:00",
        "needs_review": False,
        "enabled": True,
    }
    return McpServerRecord(**{**fields, **overrides})


def test_fingerprint_ignores_key_order_and_whitespace():
    reordered = [
        {"inputSchema": TOOL_SEARCH["inputSchema"], "description": TOOL_SEARCH["description"], "name": TOOL_SEARCH["name"]},
        TOOL_COUNT,
    ]
    assert schema_fingerprint([TOOL_SEARCH, TOOL_COUNT]) == schema_fingerprint(reordered)


def test_fingerprint_changes_on_any_reviewed_byte():
    base = schema_fingerprint([TOOL_SEARCH, TOOL_COUNT])

    description = dict(TOOL_SEARCH, description="Search the canned notes ")
    param = dict(TOOL_SEARCH, inputSchema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]})
    required = dict(TOOL_SEARCH, inputSchema={"type": "object", "properties": {"query": {"type": "string"}}})
    added_tool = [TOOL_SEARCH, TOOL_COUNT, {"name": "notes_read", "inputSchema": {"type": "object", "properties": {}}}]
    reordered_tools = [TOOL_COUNT, TOOL_SEARCH]

    assert base != schema_fingerprint([description, TOOL_COUNT])
    assert base != schema_fingerprint([param, TOOL_COUNT])
    assert base != schema_fingerprint([required, TOOL_COUNT])
    assert base != schema_fingerprint(added_tool)
    assert base != schema_fingerprint(reordered_tools)  # list order is significant


def test_fingerprint_is_stable_across_calls_and_processes_shape():
    assert schema_fingerprint([TOOL_SEARCH]) == schema_fingerprint([TOOL_SEARCH])
    assert len(schema_fingerprint([TOOL_SEARCH])) == 64


def test_fingerprint_rejects_non_serializable_values():
    with pytest.raises(TypeError):
        schema_fingerprint([{"name": "x", "schema": object()}])


def test_store_roundtrip_and_record_shape(tmp_path):
    store = McpServerStore(tmp_path)

    stored = store.add(_record())
    loaded = store.get("notes")

    assert loaded == stored
    assert loaded.command == ["python", "notes_server.py"]
    assert loaded.allowed_tools == {"notes_search"}
    assert loaded.credential_env == "LOOM_NOTES_TOKEN"  # the env NAME; a value never enters the file
    assert store.list() == [loaded]
    on_disk = json.loads((tmp_path / "mcp_servers.json").read_text(encoding="utf-8"))
    assert on_disk[0]["allowed_tools"] == ["notes_search"]


def test_duplicate_registration_refuses_and_force_overwrites_wholesale_then_freezes(tmp_path):
    store = McpServerStore(tmp_path)
    store.add(_record())

    with pytest.raises(McpError, match="--force"):
        store.add(_record())

    store.add(
        _record(
            schema_fingerprint="b" * 64,
            allowed_tools=frozenset({"notes_search", "notes_count"}),
            command=["python", "other_server.py"],
        ),
        force=True,
    )
    loaded = store.get("notes")
    assert loaded.schema_fingerprint == "b" * 64
    assert loaded.allowed_tools == {"notes_search", "notes_count"}  # the new subset, not a widening of the old
    assert loaded.command == ["python", "other_server.py"]
    assert loaded.needs_review is True and loaded.reviewed_at is None  # frozen until a human re-reviews


def test_set_status_mutates_only_the_given_fields(tmp_path):
    store = McpServerStore(tmp_path)
    store.add(_record())

    frozen = store.set_status("notes", needs_review=True)
    revoked = store.set_status("notes", enabled=False, reviewed_at="2026-11-06T10:00:00+08:00")
    refreshed = store.set_status("notes", schema_fingerprint="c" * 64, needs_review=False)

    assert frozen.needs_review is True
    assert revoked.enabled is False and revoked.reviewed_at == "2026-11-06T10:00:00+08:00"
    assert refreshed.schema_fingerprint == "c" * 64
    assert refreshed.enabled is False  # untouched by the fingerprint write
    assert refreshed.allowed_tools == {"notes_search"}  # the subset is immutable after creation
    assert store.get("notes") == refreshed


def test_set_status_and_get_refuse_unknown_servers(tmp_path):
    store = McpServerStore(tmp_path)

    with pytest.raises(KeyError):
        store.set_status("ghost", enabled=False)
    with pytest.raises(KeyError):
        store.get("ghost")


def test_mcp_servers_json_is_not_a_backup_root():
    # ADR-019's spirit: authorization-adjacent state is not silently restored.
    # A restored mcp_servers.json would resurrect registrations (frozen or
    # not) whose servers and credentials live outside the archive; a restore
    # leaves MCP unregistered, and the operator registers deliberately.
    assert "mcp_servers.json" not in BACKUP_ROOTS
