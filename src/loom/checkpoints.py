"""Sqlite checkpoint storage for resumable chat threads."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

CHECKPOINT_FILENAME = "threads.sqlite"


def _checkpoint_path(data_dir: Path) -> Path:
    ckpt_dir = Path(data_dir) / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    return ckpt_dir / CHECKPOINT_FILENAME


def make_checkpointer(data_dir: Path) -> SqliteSaver:
    """Create a SqliteSaver for short-lived graphs whose process owns cleanup."""
    conn = sqlite3.connect(_checkpoint_path(data_dir), check_same_thread=False)
    return SqliteSaver(conn)


@contextmanager
def open_checkpointer(data_dir: Path) -> Iterator[SqliteSaver]:
    """Yield a SqliteSaver whose connection closes on exit; long-lived callers use this."""
    with SqliteSaver.from_conn_string(_checkpoint_path(data_dir)) as checkpointer:
        yield checkpointer
