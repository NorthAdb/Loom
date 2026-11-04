"""Compact: fold the oldest unfolded episodic entries into the digest.

One transaction over `<data dir>/context.db` does all of it: read the oldest
unfolded entries, extend the digest, mark them folded. If anything fails the
transaction rolls back, so a failed compact keeps the old digest and the
entries stay unfolded for the next run. Compact never touches the facts
table — the digest is a summary of activity, and a summary is never allowed
to become an authoritative fact.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from loom.memory.digest import DIGEST_SCHEMA
from loom.memory.episodic import EPISODIC_SCHEMA, EpisodicEntry, _row_to_entry
from loom.ops.routines import now_local

BATCH = 100
CHAR_CAP = 4000


@dataclass(frozen=True)
class CompactResult:
    """What one compact run did; folded == 0 means there was nothing to fold."""

    old_digest: str
    new_digest: str
    folded: int


def build_digest(old: str, entries: "list[EpisodicEntry]", *, char_cap: int = CHAR_CAP) -> str:
    """Render the next digest: the old one plus one line per entry, newest last.

    When the text would exceed the cap, the oldest lines go first — the
    digest keeps the most recent context, not the full history. Pure, so
    tests pin the trimming without a database.
    """
    lines = ([old] if old else []) + [f"- {entry.ts} {entry.kind}: {entry.text}" for entry in entries]

    def total() -> int:
        return sum(len(line) + 1 for line in lines)

    while lines and total() > char_cap:
        lines.pop(0)
    return "\n".join(lines)


def compact(data_dir: Path, *, char_cap: int = CHAR_CAP, batch: int = BATCH) -> CompactResult:
    """Fold the oldest unfolded episodic entries into the digest.

    A single connection runs the read, the digest write, and the fold marks,
    and commits once; any failure rolls the whole run back.
    """
    path = Path(data_dir) / "context.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(EPISODIC_SCHEMA)
        conn.execute(DIGEST_SCHEMA)
        rows = conn.execute(
            "SELECT seq, ts, kind, text, folded FROM episodic WHERE folded = 0 ORDER BY seq ASC LIMIT ?",
            (batch,),
        ).fetchall()
        entries = [_row_to_entry(row) for row in rows]
        row = conn.execute("SELECT text FROM digest WHERE id = 1").fetchone()
        old = row[0] if row is not None else ""
        if not entries:
            return CompactResult(old_digest=old, new_digest=old, folded=0)
        new = build_digest(old, entries, char_cap=char_cap)
        conn.execute(
            "INSERT INTO digest (id, text, updated_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
            (new, now_local()),
        )
        placeholders = ",".join("?" for _ in entries)
        conn.execute(
            f"UPDATE episodic SET folded = 1 WHERE seq IN ({placeholders})",
            [entry.seq for entry in entries],
        )
        conn.commit()
        return CompactResult(old_digest=old, new_digest=new, folded=len(entries))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
