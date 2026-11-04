"""The digest: a rolling summary of compacted episodic activity.

One single-row table of `<data dir>/context.db`. The digest changes only
through `loom.memory.compact.compact` — there is no public setter, so nothing
can bypass the fold-and-mark transaction. Reading it back is all a caller
needs.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DIGEST_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS digest ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), text TEXT NOT NULL, updated_at TEXT NOT NULL)"
)


class DigestStore:
    """Read access to the digest at `<data dir>/context.db`; empty string when unset."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "context.db"

    @contextmanager
    def _connection(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        try:
            conn.execute(DIGEST_SCHEMA)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self) -> str:
        """The current digest text; empty string when nothing has been compacted yet."""
        with self._connection() as conn:
            row = conn.execute("SELECT text FROM digest WHERE id = 1").fetchone()
        return row[0] if row is not None else ""
