"""One build path for the compiled graph: chat, the routine runner, and the worker all use it."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from loom.config import Settings
from loom.graph import build_graph
from loom.llm import make_model


@contextmanager
def open_graph(settings: Settings) -> Iterator:
    """Yield the compiled graph over <data dir>/checkpoints/threads.sqlite.

    The sqlite connection opens for the with-block and closes on exit, so every
    caller (chat REPL, one routine run, one approval resume) gets the same build
    and owns its own connection.
    """
    ckpt_dir = Path(settings.loom_data_dir) / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(ckpt_dir / "threads.sqlite") as checkpointer:
        yield build_graph(make_model(settings), settings, checkpointer)
