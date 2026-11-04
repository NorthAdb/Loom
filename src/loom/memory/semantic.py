"""The semantic (Mem0) recall adapter: free-form memory behind the SemanticMemory protocol."""

import os
from pathlib import Path
from typing import Protocol

from loom.core.config import ConfigError, Settings
from loom.memory.facts import Fact


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
