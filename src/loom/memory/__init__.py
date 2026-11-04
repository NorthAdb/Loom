"""Memory layer: the authoritative facts table, the semantic (Mem0) adapter, and the Context OS.

Three layers, one injection rule. `FactsStore` is the self-owned sqlite table of
authoritative facts and preferences the owner manages through the CLI. The
`SemanticMemory` protocol covers free-form recall, backed by self-hosted Mem0
(`Mem0SemanticMemory`) or a no-op (`NullSemanticMemory`) when memory is
disabled. `build_memory_note` renders what the graph injects: facts first,
labeled authoritative; Mem0 results after, labeled reference; on conflict the
table wins. The Gate 6 context stores (`EpisodicStore`, `DigestStore`,
`FeedbackStore`, `compact`) add the working set: an episodic log, the digest it
folds into, and owner feedback, all reference-only and never authoritative.
"""

from loom.memory.compact import CompactResult, build_digest, compact
from loom.memory.context import build_context_block, build_context_note
from loom.memory.digest import DigestStore
from loom.memory.episodic import EpisodicEntry, EpisodicStore
from loom.memory.facts import Fact, FactsStore, contains_secret, has_explicit_memory_intent, looks_like_secret_value
from loom.memory.feedback import FeedbackEntry, FeedbackStore
from loom.memory.semantic import (
    Mem0SemanticMemory,
    NullSemanticMemory,
    SemanticMemory,
    build_memory_note,
    build_semantic_memory,
)

__all__ = [
    "CompactResult",
    "DigestStore",
    "EpisodicEntry",
    "EpisodicStore",
    "Fact",
    "FactsStore",
    "FeedbackEntry",
    "FeedbackStore",
    "Mem0SemanticMemory",
    "NullSemanticMemory",
    "SemanticMemory",
    "build_context_block",
    "build_context_note",
    "build_digest",
    "build_memory_note",
    "build_semantic_memory",
    "compact",
    "contains_secret",
    "has_explicit_memory_intent",
    "looks_like_secret_value",
]
