"""Owner feedback: short corrections the operator records for future turns.

One table of `<data dir>/context.db`, written only through the CLI
(`loom memory feedback`) by the owner themselves. Entries are injected as
reference in later turns, capped at 2000 characters like audit values, and
refused when the text carries a secret-shaped value. Feedback never writes
the facts table.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loom.memory.facts import contains_secret
from loom.ops.routines import now_local

_TEXT_CAP = 2000

FEEDBACK_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS feedback ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, source TEXT NOT NULL, text TEXT NOT NULL)"
)


@dataclass(frozen=True)
class FeedbackEntry:
    """One recorded owner correction."""

    seq: int
    ts: str
    source: str
    text: str


class FeedbackStore:
    """Append-only owner feedback at `<data dir>/context.db`."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "context.db"

    @contextmanager
    def _connection(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        try:
            conn.execute(FEEDBACK_SCHEMA)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def add(self, source: str, text: str) -> FeedbackEntry:
        """Record one feedback entry; returns the stored row.

        The text is truncated at 2000 characters and refused when it looks
        like it carries a secret, the same guard the facts table applies.
        """
        if contains_secret(text):
            raise ValueError("refusing to store feedback: text looks like it carries a secret")
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO feedback (ts, source, text) VALUES (?, ?, ?)",
                (now_local(), source, text[:_TEXT_CAP]),
            )
            seq = cursor.lastrowid
        return self.get(seq)

    def get(self, seq: int) -> FeedbackEntry:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT seq, ts, source, text FROM feedback WHERE seq = ?", (seq,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no such feedback entry: {seq}")
        return FeedbackEntry(seq=row[0], ts=row[1], source=row[2], text=row[3])

    def recent(self, limit: int = 10) -> "list[FeedbackEntry]":
        """The newest feedback first."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT seq, ts, source, text FROM feedback ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [FeedbackEntry(seq=row[0], ts=row[1], source=row[2], text=row[3]) for row in rows]
