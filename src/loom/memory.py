"""Memory layer: the authoritative facts table and the semantic (Mem0) adapter.

Two stores, one injection rule. `FactsStore` is the self-owned sqlite table of
authoritative facts and preferences the owner manages through the CLI. The
`SemanticMemory` protocol covers free-form recall, backed by self-hosted Mem0
(`Mem0SemanticMemory`) or a no-op (`NullSemanticMemory`) when memory is
disabled. `build_memory_note` renders what the graph injects: facts first,
labeled authoritative; Mem0 results after, labeled reference; on conflict the
table wins.
"""

import math
import os
import re
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loom.config import ConfigError, Settings
from loom.logging import is_sensitive
from loom.routines import now_local

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
# Words in plain casing (lower/upper/title) are prose, not tokens; anything
# else a shape captures is treated as a value worth refusing.
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

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.execute(_FACTS_SCHEMA)
        return conn

    def upsert(self, key: str, value: str, source: str = "human", confidence: float = 1.0) -> Fact:
        """Insert or correct one fact; returns the stored row."""
        if is_sensitive(key):
            raise ValueError(f"refusing to store fact: key {key!r} looks like it carries a secret")
        if contains_secret(f"{key} {value}"):
            raise ValueError(f"refusing to store fact: value for {key!r} looks like an API key or token")
        if looks_like_secret_value(value):
            raise ValueError(f"refusing to store fact: value for {key!r} looks like it carries a secret")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO facts (id, key, value, source, updated_at, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source, "
                "updated_at=excluded.updated_at, confidence=excluded.confidence",
                (uuid.uuid4().hex, key, value, source, now_local(), confidence),
            )
        return self.get(key)

    def get(self, key: str) -> Fact | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, key, value, source, updated_at, confidence FROM facts WHERE key = ?", (key,)
            ).fetchone()
        return _row_to_fact(row)

    def list(self) -> list[Fact]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, key, value, source, updated_at, confidence FROM facts ORDER BY key"
            ).fetchall()
        return [_row_to_fact(row) for row in rows]

    def delete(self, key: str) -> Fact:
        """Remove one fact and return the removed row; KeyError when absent."""
        with self._connect() as conn:
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


class SemanticMemory(Protocol):
    """Free-form recall store: add raw texts, search by query."""

    def add(self, texts: list[str]) -> None:
        ...

    def search(self, query: str, limit: int = 3) -> list[str]:
        ...


class NullSemanticMemory:
    """Semantic memory disabled: writes are dropped, searches come back empty."""

    def add(self, texts: list[str]) -> None:
        return None

    def search(self, query: str, limit: int = 3) -> list[str]:
        return []


_MEM0_USER_ID = "owner"  # single-user deployment: one constant scope


def _mem0_error(exc: Exception, api_key: str) -> RuntimeError:
    """Wrap an adapter failure as RuntimeError; the message never carries the key."""
    message = f"semantic memory call failed: {type(exc).__name__}: {exc}"
    if api_key:
        message = message.replace(api_key, "***")
    return RuntimeError(message)


class Mem0SemanticMemory:
    """Thin adapter over self-hosted Mem0 (openai-compatible LLM + embedder, local qdrant).

    The endpoint must serve BOTH chat completions and embeddings. Telemetry is
    disabled by default through MEM0_TELEMETRY before mem0 is imported (it is
    read at import time); an explicit MEM0_TELEMETRY=True from the operator wins.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise ConfigError("LLM_API_KEY is empty; set it in .env or the environment to use semantic memory")
        base = Path(settings.loom_data_dir) / "memory"
        base.mkdir(parents=True, exist_ok=True)
        llm_config = {
            "model": settings.llm_model,
            "api_key": settings.llm_api_key,
            "temperature": 0,
        }
        if settings.llm_base_url:
            llm_config["openai_base_url"] = settings.llm_base_url
            embedder_base_url = settings.llm_base_url
        else:
            embedder_base_url = "https://api.openai.com/v1"
        self._api_key = settings.llm_api_key
        try:
            os.environ.setdefault("MEM0_TELEMETRY", "False")
            from mem0 import Memory
            from mem0.configs.base import MemoryConfig
            from mem0.embeddings.configs import EmbedderConfig
            from mem0.llms.configs import LlmConfig
            from mem0.vector_stores.configs import VectorStoreConfig

            config = MemoryConfig(
                llm=LlmConfig(provider="openai", config=llm_config),
                embedder=EmbedderConfig(
                    provider="openai",
                    config={"api_key": settings.llm_api_key, "openai_base_url": embedder_base_url},
                ),
                vector_store=VectorStoreConfig(provider="qdrant", config={"path": str(base), "on_disk": True}),
                history_db_path=str(base / "history.db"),
            )
            self._memory = Memory(config)
        except Exception as exc:
            raise _mem0_error(exc, self._api_key) from exc

    def add(self, texts: list[str]) -> None:
        for text in texts:
            try:
                self._memory.add(text, user_id=_MEM0_USER_ID)
            except Exception as exc:
                raise _mem0_error(exc, self._api_key) from exc

    def search(self, query: str, limit: int = 3) -> list[str]:
        try:
            result = self._memory.search(query, filters={"user_id": _MEM0_USER_ID}, top_k=limit)
        except Exception as exc:
            raise _mem0_error(exc, self._api_key) from exc
        return [str(item.get("memory", "")) for item in result.get("results", []) if item.get("memory")]


def build_semantic_memory(settings: Settings) -> SemanticMemory:
    """Build the semantic layer from settings; the one seam callers and tests use.

    Disabled memory (LOOM_MEMORY_ENABLED=false) returns the no-op store, so the
    graph and CLI work without a Mem0 endpoint or even the mem0 package import.
    """
    if not settings.memory_enabled:
        return NullSemanticMemory()
    return Mem0SemanticMemory(settings)


def build_memory_note(facts: list[Fact], memories: list[str], limit: int = 5) -> str:
    """Render the per-turn injected note: facts first (权威), memories after (参考).

    The facts table is authoritative; the note tells the model to prefer it on
    conflict. At most `limit` entries total. Empty in, empty string out.
    """
    entries = [f"[权威] {fact.key}: {fact.value}" for fact in facts]
    entries += [f"[参考] {text}" for text in memories]
    entries = entries[:limit]
    if not entries:
        return ""
    header = (
        "Long-term memory about the user. Entries marked 权威 are authoritative "
        "facts; entries marked 参考 are reference only. On conflict, prefer the 权威 facts."
    )
    return header + "\n" + "\n".join(f"- {entry}" for entry in entries)
