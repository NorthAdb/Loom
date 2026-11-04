"""One build path for the compiled graph: chat, the routine runner, and the worker all use it."""

from collections.abc import Iterator
from contextlib import contextmanager

from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.core.llm import make_model
from loom.ops.checkpoints import open_checkpointer


@contextmanager
def open_graph(settings: Settings) -> Iterator:
    """Yield the compiled graph over the checkpoint sqlite file.

    The sqlite connection opens for the with-block and closes on exit, so every
    caller (chat REPL, one routine run, one approval resume) gets the same build
    and owns its own connection.
    """
    with open_checkpointer(settings.loom_data_dir) as checkpointer:
        yield build_graph(make_model(settings), settings, checkpointer)
