"""The schema fingerprint: one stable hash over an MCP server's advertised tool list.

Registered at `loom mcp register`, re-verified live before every call, and the
basis of the freeze: a fingerprint that drifts means the server's tool surface
changed, and nobody has reviewed the new surface yet (ADR-020).
"""

import hashlib
import json


def schema_fingerprint(tools: "list[dict]") -> str:
    """Hash the tool list to a stable 64-hex fingerprint.

    Normalization: the canonical form is `json.dumps(tools, sort_keys=True,
    separators=(",", ":"), ensure_ascii=False)`, hashed with sha256. Dictionary
    key order and whitespace never matter, so the same schema with its keys in
    any order hashes equal. List order is significant: a server that reorders
    its tools changes the fingerprint. Any other difference — one character of
    a description, a parameter name, an entry added to or removed from a
    required list, a new or removed tool — hashes different. A tools value that
    is not JSON-serializable raises TypeError.
    """
    canonical = json.dumps(tools, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
