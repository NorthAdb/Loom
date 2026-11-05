"""The MCP bridge: registered servers become gated ToolSpecs, and the register/review flows.

Every tool body re-reads the record (the build-time record may be stale),
re-checks the allowed subset, and re-verifies the schema fingerprint live
against the server before anything is called — defense in depth under the
Tool Gateway, which still runs first because every MCP spec is
side_effect=True. Tool return values are untrusted data: the body scrubs any
known credential value out of the text and hands the rest to the model,
writing nothing to any store. The bridge's own rejected rows are deliberately
correlation-less: the scope correlation is the gateway's business, and these
rows record the bridge's independent vetoes.

Names: the registry key, the allowed_actions entry, and the audit action are
one canonical string `mcp:<server_id>:<tool>`. Provider APIs reject colons in
function names, so the binding name is the mapped form (colons to
underscores); the mapping is validated injective at registration and build
time, and `canonical_action_name` is its single reverse.
"""

import os
import re

from langchain_core.tools import StructuredTool

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.ops.routines import now_local
from loom.tools.mcp.client import McpClient
from loom.tools.mcp.fingerprint import schema_fingerprint
from loom.tools.mcp.registry import McpError, McpServerRecord, McpServerStore
from loom.tools.registry import ToolSpec

# The model-facing schema is a pass-through object: the registry never probes
# a server at build time, so the real parameter schema is only known live.
_ARGS_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}

_SERVER_ID = re.compile(r"[a-z0-9][a-z0-9_-]*")


def canonical_action(server_id: str, tool_name: str) -> str:
    """The registry key, allowed_actions entry, and audit action for one tool."""
    return f"mcp:{server_id}:{tool_name}"


def model_action_name(canonical: str) -> str:
    """The model-facing binding name for one canonical key: colons become underscores."""
    return canonical.replace(":", "_")


def canonical_action_name(model_name: str, registry: "dict[str, object]") -> "str | None":
    """The one canonical key a model-proposed name resolves to, or None.

    Exact keys pass through unchanged; the mapped mcp_ form maps back to its
    single canonical key. Unknown or ambiguous names return None.
    """
    if model_name in registry:
        return model_name
    if not model_name.startswith("mcp_"):
        return None
    matches = [key for key in registry if model_action_name(key) == model_name]
    return matches[0] if len(matches) == 1 else None


def _reject(audit: AuditLog, server_id: str, tool_name: str, reason: str) -> None:
    """One rejected row for a bridge-level veto; no correlation_id by design."""
    audit.append(
        run_id="",
        actor="mcp",
        tool=canonical_action(server_id, tool_name),
        args={"server_id": server_id, "tool": tool_name, "reason": reason},
        decision="rejected",
    )


def _scrub(text: str, settings: Settings) -> str:
    """Known credential values never reach the model: they become *** first.

    Every registered server's credential value is read from the parent's own
    environment and replaced longest-first. With the minimal child
    environment a server can only ever hold its own secret; scrubbing all of
    them closes the argument-echo channel too.
    """
    values = set()
    for record in McpServerStore(settings.loom_data_dir).list():
        if record.credential_env:
            value = os.environ.get(record.credential_env, "")
            if value:
                values.add(value)
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "***")
    return text


def mcp_tool_spec(
    server: McpServerRecord, tool_name: str, settings: Settings, *, client_factory=McpClient
) -> ToolSpec:
    """One side-effect ToolSpec for `tool_name` on `server`; the registry key is the mcp: name."""
    server_id = server.server_id

    def _call(**kwargs) -> str:
        store = McpServerStore(settings.loom_data_dir)
        audit = AuditLog(settings.loom_data_dir)
        try:
            record = store.get(server_id)
        except KeyError:
            raise McpError(f"mcp server '{server_id}' is not registered or is revoked") from None
        if not record.enabled:
            raise McpError(f"mcp server '{server_id}' is not registered or is revoked")
        if record.needs_review:
            reason = (
                f"mcp server '{server_id}' is frozen: schema changed or forced re-registration; "
                f"run `loom mcp review {server_id}`"
            )
            _reject(audit, server_id, tool_name, reason)
            raise McpError(reason)
        if tool_name not in record.allowed_tools:
            reason = f"tool '{tool_name}' is not in the allowed subset for '{server_id}'"
            _reject(audit, server_id, tool_name, reason)
            raise McpError(reason)
        client = client_factory(record.command, credential_env=record.credential_env)
        client.start()
        try:
            live = schema_fingerprint(client.list_tools())
            if live != record.schema_fingerprint:
                store.set_status(server_id, needs_review=True)
                reason = f"schema fingerprint mismatch for '{server_id}'; the server is frozen pending re-review"
                _reject(audit, server_id, tool_name, reason)
                raise McpError(reason)
            # Untrusted-return contract: the text is returned to the model as
            # data — no facts upsert, no scope change, no store write here,
            # and no known credential value survives the scrub.
            return _scrub(client.call_tool(tool_name, dict(kwargs)), settings)
        finally:
            client.stop()

    func = StructuredTool.from_function(
        func=_call,
        name=model_action_name(canonical_action(server_id, tool_name)),
        description=f"MCP tool {server_id}:{tool_name} ({server.name}); every call is re-verified and audited.",
        args_schema=_ARGS_SCHEMA,
    )
    return ToolSpec(func, side_effect=True)


def build_mcp_registry(settings: Settings, transports: dict | None = None) -> dict[str, ToolSpec]:
    """One spec per allowed tool of every enabled record; {} when no store file exists.

    Registry keys are the action names (`mcp:<server_id>:<tool>`) that Request
    Scopes allow and the audit records. Servers are NOT probed here: no
    subprocess at build time — the fingerprint check runs at call time inside
    the tool body. `transports` is accepted for signature symmetry with
    make_tools and unused: MCP servers own their transport.
    """
    store = McpServerStore(settings.loom_data_dir)
    registry: dict[str, ToolSpec] = {}
    mapped: dict[str, str] = {}
    for record in store.list():
        if not record.enabled:
            continue
        for tool_name in sorted(record.allowed_tools):
            key = canonical_action(record.server_id, tool_name)
            binding = model_action_name(key)
            if binding in mapped:
                raise McpError(
                    f"mcp action binding {binding!r} is ambiguous between {mapped[binding]!r} and {key!r}; "
                    "rename the server or the tool"
                )
            mapped[binding] = key
            registry[key] = mcp_tool_spec(record, tool_name, settings)
    return registry


def register_server(
    store: McpServerStore,
    *,
    server_id: str,
    name: str,
    command: "list[str]",
    allowed_tools: "list[str]",
    credential_env: str = "",
    force: bool = False,
    client_factory=McpClient,
) -> McpServerRecord:
    """Probe the server once, validate the subset, and store the record.

    Every allowed tool must exist in the server's live tool listing — a typo
    refuses, naming the tools the server actually offers. The stored
    fingerprint is computed from that listing. A duplicate id refuses unless
    force=True, and a forced overwrite freezes the new record until a human
    re-reviews. A server_id must be lowercase letters, digits, hyphens, and
    underscores, and a new action whose model-facing binding name collides
    with an already-registered action refuses, so the name mapping stays
    reversible. The caller (the CLI) audits.
    """
    if not _SERVER_ID.fullmatch(server_id):
        raise McpError(
            f"server_id {server_id!r} must be lowercase letters, digits, hyphens, or underscores "
            "(it forms the mcp:<server_id>:<tool> action name)"
        )
    existing = {
        model_action_name(canonical_action(record.server_id, tool))
        for record in store.list()
        if record.server_id != server_id
        for tool in record.allowed_tools
    }
    collisions = sorted(
        model_action_name(canonical_action(server_id, tool)) for tool in allowed_tools
        if model_action_name(canonical_action(server_id, tool)) in existing
    )
    if collisions:
        raise McpError(
            f"mcp action binding(s) {', '.join(collisions)} collide with an already-registered server's tools; "
            "rename the server or the tool"
        )
    client = client_factory(list(command), credential_env=credential_env)
    client.start()
    try:
        client.initialize()
        tools = client.list_tools()
    finally:
        client.stop()
    listed = sorted(str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name"))
    unknown = sorted(set(allowed_tools) - set(listed))
    if unknown:
        raise McpError(
            f"tool(s) {', '.join(unknown)} are not offered by '{server_id}'; the server lists: {', '.join(listed)}"
        )
    record = McpServerRecord(
        server_id=server_id,
        name=name,
        command=list(command),
        transport="stdio",
        schema_fingerprint=schema_fingerprint(tools),
        allowed_tools=frozenset(allowed_tools),
        credential_env=credential_env,
        reviewed_at=now_local(),
        needs_review=False,
        enabled=True,
    )
    return store.add(record, force=force)


def review_server(store: McpServerStore, server_id: str, *, client_factory=McpClient) -> "tuple[McpServerRecord, dict]":
    """Re-probe a frozen server, refresh the fingerprint, and clear the freeze.

    Returns (record, diff). The diff is what the human reviews before
    trusting the unfreeze: the old and new fingerprints and the tool names
    the server added or removed relative to the registered subset (the subset
    itself never widens here; changing it is a --force re-registration).
    """
    record = store.get(server_id)
    client = client_factory(record.command, credential_env=record.credential_env)
    client.start()
    try:
        client.initialize()
        tools = client.list_tools()
    finally:
        client.stop()
    new_fingerprint = schema_fingerprint(tools)
    listed = sorted(str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name"))
    diff = {
        "old_fingerprint": record.schema_fingerprint,
        "new_fingerprint": new_fingerprint,
        "added": sorted(set(listed) - set(record.allowed_tools)),
        "removed": sorted(set(record.allowed_tools) - set(listed)),
        "listed": listed,
    }
    updated = store.set_status(
        server_id,
        schema_fingerprint=new_fingerprint,
        needs_review=False,
        reviewed_at=now_local(),
    )
    return updated, diff
