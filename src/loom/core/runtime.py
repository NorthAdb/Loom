"""One build path for the compiled graph: chat, the routine runner, and the worker all use it."""

from collections.abc import Iterator
from contextlib import contextmanager

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.core.llm import InertChatModel, make_model
from loom.gateway.scope import ScopeStore
from loom.gateway.tool_gateway import ToolGateway
from loom.ops.checkpoints import open_checkpointer
from loom.tools.mcp import build_mcp_registry


@contextmanager
def open_graph(settings: Settings, *, mcp: bool = False, allow_missing_llm: bool = False) -> Iterator:
    """Yield the compiled graph over the checkpoint sqlite file.

    The sqlite connection opens for the with-block and closes on exit, so every
    caller (chat REPL, one routine run, one approval resume) gets the same build
    and owns its own connection. Every build carries the Tool Gateway: unscoped
    runs (the operator's own chat and routines) keep the Gate 1-5 human gate,
    and a run that carries a scope_id — an inbound triage paused into the
    pending queue and resumed through the CLI or the console — is re-checked
    against its scope at the gate and again at execution.

    `mcp=True` merges the registered MCP servers' tools into the registry
    (ADR-020). The default is False and every entrypoint decides deliberately:
    chat, routines, and triage keep MCP off until a caller flips the flag, so
    an unregistered capability is a build-time decision, not a runtime drift.

    `allow_missing_llm=True` builds the graph with the inert model when no
    LLM key is configured. Only the reject path of an approval resume passes
    it: the run goes gate -> aborted -> END and never invokes the model, so a
    human refusal must not depend on model availability.
    """
    gateway = ToolGateway(
        AuditLog(settings.loom_data_dir, settings=settings), ScopeStore(settings.loom_data_dir), allow_unscoped=True
    )
    extra_registry = build_mcp_registry(settings) if mcp else None
    if allow_missing_llm and not settings.llm_api_key:
        model: object = InertChatModel()
    else:
        model = make_model(settings)
    with open_checkpointer(settings.loom_data_dir) as checkpointer:
        yield build_graph(model, settings, checkpointer, gateway=gateway, extra_registry=extra_registry)
