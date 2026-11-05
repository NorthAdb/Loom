"""The MCP bridge: registered servers become gated ToolSpecs, and the register/review flows.

Every tool body re-reads the record (the build-time record may be stale),
re-checks the allowed subset, and re-verifies the schema fingerprint live
against the server before anything is called — defense in depth under the
Tool Gateway, which still runs first because every MCP spec is
side_effect=True. Tool return values are untrusted data: the body hands the
server's text to the model and writes nothing to any store. The bridge's own
rejected rows are deliberately correlation-less: the scope correlation is the
gateway's business, and these rows record the bridge's independent vetoes.
"""

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


def _reject(audit: AuditLog, server_id: str, tool_name: str, reason: str) -> None:
    """One rejected row for a bridge-level veto; no correlation_id by design."""
    audit.append(
        run_id="",
        actor="mcp",
        tool=f"mcp:{server_id}:{tool_name}",
        args={"server_id": server_id, "tool": tool_name, "reason": reason},
        decision="rejected",
    )


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
            # data — no facts upsert, no scope change, no store write here.
            return client.call_tool(tool_name, dict(kwargs))
        finally:
            client.stop()

    func = StructuredTool.from_function(
        func=_call,
        name=f"mcp_{server_id}_{tool_name}",
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
    for record in store.list():
        if not record.enabled:
            continue
        for tool_name in sorted(record.allowed_tools):
            registry[f"mcp:{record.server_id}:{tool_name}"] = mcp_tool_spec(record, tool_name, settings)
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
    re-reviews. The caller (the CLI) audits.
    """
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


def review_server(store: McpServerStore, server_id: str, *, client_factory=McpClient) -> McpServerRecord:
    """Re-probe a frozen server, refresh the fingerprint, and clear the freeze."""
    record = store.get(server_id)
    client = client_factory(record.command, credential_env=record.credential_env)
    client.start()
    try:
        client.initialize()
        tools = client.list_tools()
    finally:
        client.stop()
    return store.set_status(
        server_id,
        schema_fingerprint=schema_fingerprint(tools),
        needs_review=False,
        reviewed_at=now_local(),
    )
