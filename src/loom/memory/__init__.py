"""Memory layer: the authoritative facts table and the semantic (Mem0) adapter.

Two stores, one injection rule. `FactsStore` is the self-owned sqlite table of
authoritative facts and preferences the owner manages through the CLI. The
`SemanticMemory` protocol covers free-form recall, backed by self-hosted Mem0
(`Mem0SemanticMemory`) or a no-op (`NullSemanticMemory`) when memory is
disabled. `build_memory_note` renders what the graph injects: facts first,
labeled authoritative; Mem0 results after, labeled reference; on conflict the
table wins.
"""

from loom.memory.facts import Fact, FactsStore, contains_secret, has_explicit_memory_intent, looks_like_secret_value
from loom.memory.semantic import (
    Mem0SemanticMemory,
    NullSemanticMemory,
    SemanticMemory,
    build_memory_note,
    build_semantic_memory,
)

__all__ = [
    "Fact",
    "FactsStore",
    "Mem0SemanticMemory",
    "NullSemanticMemory",
    "SemanticMemory",
    "build_memory_note",
    "build_semantic_memory",
    "contains_secret",
    "has_explicit_memory_intent",
    "looks_like_secret_value",
]
