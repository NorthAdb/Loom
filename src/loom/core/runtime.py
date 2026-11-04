"""One build path for the compiled graph: chat, the routine runner, and the worker all use it."""

from collections.abc import Iterator
from contextlib import contextmanager

from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.core.llm import make_model
from loom.gateway.scope import ScopeStore
from loom.gateway.tool_gateway import ToolGateway
from loom.ops.checkpoints import open_checkpointer


@contextmanager
def open_graph(settings: Settings) -> Iterator:
    """Yield the compiled graph over the checkpoint sqlite file.

    The sqlite connection opens for the with-block and closes on exit, so every
    caller (chat REPL, one routine run, one approval resume) gets the same build
    and owns its own connection. Every build carries the Tool Gateway: unscoped
    runs (the operator's own chat and routines) keep the Gate 1-5 human gate,
    and a run that carries a scope_id — an inbound triage paused into the
    pending queue and resumed through the CLI or the console — is re-checked
    against its scope at the gate and again at execution.
    """
    gateway = ToolGateway(AuditLog(settings.loom_data_dir), ScopeStore(settings.loom_data_dir), allow_unscoped=True)
    with open_checkpointer(settings.loom_data_dir) as checkpointer:
        yield build_graph(make_model(settings), settings, checkpointer, gateway=gateway)
