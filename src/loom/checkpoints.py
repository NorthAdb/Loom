"""Sqlite checkpoint storage for resumable chat threads."""

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def make_checkpointer(data_dir: Path) -> SqliteSaver:
    """Create a SqliteSaver over <data dir>/checkpoints/threads.sqlite."""
    ckpt_dir = Path(data_dir) / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ckpt_dir / "threads.sqlite", check_same_thread=False)
    return SqliteSaver(conn)
