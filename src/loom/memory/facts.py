"""The authoritative facts table: the sqlite FactsStore, the Fact row, and the secret checks that guard writes."""

import math
import re
import sqlite3
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loom.core.logging import is_sensitive
from loom.ops.routines import now_local

_FACTS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS facts ("
    "id TEXT PRIMARY KEY, key TEXT UNIQUE, value TEXT, source TEXT, "
    "updated_at TEXT, confidence REAL)"
)

# Write-back triggers for post_turn. Keep in sync with README (Memory).
_EXPLICIT_PATTERNS = (
    "记住", "以后", "总是", "喜欢", "偏好", "不要再",
    "remember", "always", "prefer", "from now on",
)

_SECRET_PREFIXES = ("sk-", "pk_", "ghp_", "akia", "xox")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_-]{24,}")
_SECRET_WORD = re.compile(r"key|token|secret|password", re.IGNORECASE)

_HIGH_ENTROPY_TOKEN = re.compile("[A-Za-z0-9+/=_.-]{16,}")
_HIGH_ENTROPY_BITS = 3.25
_UUID_OR_HEX = "0123456789abcdefABCDEF-"
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
_PASSWORD_SHAPE = re.compile(r"\b(?:password|passwd|pwd)\b\s*[:=]?\s*(\S+)", re.IGNORECASE)


def _shannon_entropy(text: str) -> float:
    """Bits of information per character of this exact string."""
    if not text:
        return 0.0
    length = len(text)
    return -sum((count / length) * math.log2(count / length) for count in Counter(text).values())


def _is_plain_word(run: str) -> bool:
    """Whether this run is a naturally cased word (lower, upper, or title)."""
    return run.isalpha() and (run.islower() or run.isupper() or run.istitle())


def _looks_passwordish(token: str) -> bool:
    """Whether a password-labeled value reads as a credential, not as prose.

    A digit or a case mix is the credential signature; punctuation and plain
    words after "password" are sentences ("I forgot my password yesterday").
    """
    if _is_plain_word(token):
        return False
    return any(ch.isdigit() for ch in token) or (
        any(ch.isupper() for ch in token) and any(ch.islower() for ch in token)
    )


def looks_like_secret_value(value: str) -> bool:
    """Report whether a facts value carries a secret beyond the chat guard's shapes.

    Refuses any token-shaped run of >= 16 ASCII characters (base64/hex/alnum
    shapes) whose Shannon entropy is >= 3.25 bits/char, and any
    "password xxx" / "passwd: xxx" / "pwd=xxx" shape whose captured value
    itself looks like a credential. Plain words, UUID/hex-dash shapes, and
    URLs (stripped before scanning: their long path slugs are just addresses)
    never refuse. CJK prose never forms these runs and always passes.
    """
    text = _URL.sub(" ", value)
    for run in _HIGH_ENTROPY_TOKEN.findall(text):
        if _is_plain_word(run) or all(ch in _UUID_OR_HEX for ch in run):
            continue
        if _shannon_entropy(run) >= _HIGH_ENTROPY_BITS:
            return True
    return any(_looks_passwordish(match.group(1)) for match in _PASSWORD_SHAPE.finditer(text))


def has_explicit_memory_intent(text: str) -> bool:
    """Report whether a chat message explicitly asks Loom to remember something."""
    lowered = text.lower()
    return any(pattern in lowered for pattern in _EXPLICIT_PATTERNS)


def contains_secret(text: str) -> bool:
    """Report whether text looks like it carries an API key or token.

    Refuses a value with a provider-style prefix (sk- / pk_ / ghp_ / AKIA / xox)
    or any long token-like run in a message that also contains one of the words
    key/token/secret/password (co-occurrence anywhere, not proximity).
    """
    for token in text.split():
        if token.lower().startswith(_SECRET_PREFIXES):
            return True
    return bool(_LONG_TOKEN.search(text) and _SECRET_WORD.search(text))


@dataclass(frozen=True)
class Fact:
    """One authoritative row of the facts table."""

    id: str
    key: str
    value: str
    source: str
    updated_at: str
    confidence: float


class FactsStore:
    """Sqlite-backed facts and preferences at <data dir>/facts.db.

    Same key upserts again means a correction: value, source, updated_at, and
    confidence are overwritten, the row id stays. Upsert refuses anything that
    looks like a secret, so keys and tokens never enter memory.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "facts.db"

    @contextmanager
    def _connection(self):
        """A facts-db connection that commits on success and always closes.

        sqlite3's own `with` only manages the transaction; without an explicit
        close Windows holds facts.db until garbage collection runs, which
        blocked tests (and any file-level consumer of the data dir) on GC luck.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        try:
            conn.execute(_FACTS_SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert(self, key: str, value: str, source: str = "human", confidence: float = 1.0) -> Fact:
        """Insert or correct one fact; returns the stored row."""
        if is_sensitive(key):
            raise ValueError(f"refusing to store fact: key {key!r} looks like it carries a secret")
        if contains_secret(f"{key} {value}"):
            raise ValueError(f"refusing to store fact: value for {key!r} looks like an API key or token")
        if looks_like_secret_value(value):
            raise ValueError(f"refusing to store fact: value for {key!r} looks like it carries a secret")
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO facts (id, key, value, source, updated_at, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source, "
                "updated_at=excluded.updated_at, confidence=excluded.confidence",
                (uuid.uuid4().hex, key, value, source, now_local(), confidence),
            )
        return self.get(key)

    def get(self, key: str) -> Fact | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, key, value, source, updated_at, confidence FROM facts WHERE key = ?", (key,)
            ).fetchone()
        return _row_to_fact(row)

    def list(self) -> list[Fact]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, key, value, source, updated_at, confidence FROM facts ORDER BY key"
            ).fetchall()
        return [_row_to_fact(row) for row in rows]

    def delete(self, key: str) -> Fact:
        """Remove one fact and return the removed row; KeyError when absent."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, key, value, source, updated_at, confidence FROM facts WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no fact named {key!r}")
            conn.execute("DELETE FROM facts WHERE key = ?", (key,))
        return _row_to_fact(row)


def _row_to_fact(row: tuple) -> Fact | None:
    if row is None:
        return None
    return Fact(id=row[0], key=row[1], value=row[2], source=row[3], updated_at=row[4], confidence=row[5])
