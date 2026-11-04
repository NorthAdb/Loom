"""The episodic log: what happened, stored as append-only events.

One table of `<data dir>/context.db`. Entries are plain records of activity
(chat turns, later inbound events), capped at 2000 characters like audit
values, and refused when the text carries a secret-shaped value. Entries are
input for `loom.memory.compact`, never authoritative: only the facts table is.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loom.memory.facts import contains_secret
from loom.ops.routines import now_local

_TEXT_CAP = 2000

EPISODIC_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS episodic ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, "
    "text TEXT NOT NULL, folded INTEGER NOT NULL DEFAULT 0)"
)


@dataclass(frozen=True)
class EpisodicEntry:
    """One recorded event; folded entries have been compacted into the digest."""

    seq: int
    ts: str
    kind: str
    text: str
    folded: bool


def _row_to_entry(row: tuple) -> EpisodicEntry:
    return EpisodicEntry(seq=row[0], ts=row[1], kind=row[2], text=row[3], folded=bool(row[4]))


class EpisodicStore:
    """Append-only episodic events at `<data dir>/context.db`."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "context.db"

    @contextmanager
    def _connection(self):
        """A context-db connection that commits on success, rolls back on error, and always closes."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        try:
            conn.execute(EPISODIC_SCHEMA)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def append(self, kind: str, text: str) -> EpisodicEntry:
        """Record one event; returns the stored entry.

        The text is truncated at 2000 characters and refused when it looks
        like it carries a secret, the same guard the facts table applies.
        """
        if contains_secret(text):
            raise ValueError(f"refusing to log {kind!r} entry: text looks like it carries a secret")
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO episodic (ts, kind, text) VALUES (?, ?, ?)",
                (now_local(), kind, text[:_TEXT_CAP]),
            )
            seq = cursor.lastrowid
        return self.get(seq)

    def get(self, seq: int) -> EpisodicEntry:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT seq, ts, kind, text, folded FROM episodic WHERE seq = ?", (seq,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no such episodic entry: {seq}")
        return _row_to_entry(row)

    def recent(self, limit: int = 10, *, unfolded_only: bool = True) -> "list[EpisodicEntry]":
        """The newest entries first, optionally skipping ones already compacted."""
        query = "SELECT seq, ts, kind, text, folded FROM episodic"
        if unfolded_only:
            query += " WHERE folded = 0"
        query += " ORDER BY seq DESC LIMIT ?"
        with self._connection() as conn:
            rows = conn.execute(query, (limit,)).fetchall()
        return [_row_to_entry(row) for row in rows]

    def oldest_unfolded(self, limit: int = 100) -> "list[EpisodicEntry]":
        """The oldest unfolded entries, first-in first-out; compact's input."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT seq, ts, kind, text, folded FROM episodic WHERE folded = 0 ORDER BY seq ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_entry(row) for row in rows]

    def count_unfolded(self) -> int:
        with self._connection() as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM episodic WHERE folded = 0").fetchone()
        return count

    def mark_folded(self, seqs: "list[int]") -> None:
        """Flag entries as folded into the digest; the caller owns the transaction."""
        if not seqs:
            return
        placeholders = ",".join("?" for _ in seqs)
        with self._connection() as conn:
            conn.execute(f"UPDATE episodic SET folded = 1 WHERE seq IN ({placeholders})", seqs)
