"""The MCP server registry: one record per registered server over `<data dir>/mcp_servers.json`.

The store is pure like ScopeStore: it persists records and mutates them only
through `add` and `set_status`, and keeps no audit log of its own — the CLI
layer audits. `allowed_tools` is immutable after creation: `set_status` cannot
touch it, so changing the subset means re-registering with force, which
freezes the new record (needs_review) until a human re-reviews. No silent
widening.
"""

import json
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from loom.ops.routines import write_json_atomic


class McpError(ValueError):
    """Raised when an MCP server would be registered, mutated, or called illegally."""


@dataclass(frozen=True)
class McpServerRecord:
    """One registered MCP server; see ADR-020 for the lifecycle.

    `command` is the argv list that starts the server (stdio transport).
    `credential_env` is the NAME of the environment variable holding the
    server's credential — never the value, which lives only in the operator's
    environment. `schema_fingerprint` is the hash the registration probed; the
    bridge re-verifies it live before every call.
    """

    server_id: str
    name: str
    command: "list[str]"
    transport: str
    schema_fingerprint: str
    allowed_tools: frozenset[str]
    credential_env: str = ""
    reviewed_at: str | None = None
    needs_review: bool = False
    enabled: bool = True

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["command"] = list(self.command)
        payload["allowed_tools"] = sorted(self.allowed_tools)
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "McpServerRecord":
        data = dict(payload)
        data["command"] = list(data["command"])
        data["allowed_tools"] = frozenset(data["allowed_tools"])
        return cls(**data)


class McpServerStore:
    """CRUD over `<data dir>/mcp_servers.json`; writes serialized per process like the other stores."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "mcp_servers.json"
        self._lock = threading.Lock()

    def _load(self) -> "list[McpServerRecord]":
        if not self._path.exists():
            return []
        items = json.loads(self._path.read_text(encoding="utf-8"))
        return [McpServerRecord.from_dict(item) for item in items]

    def _write(self, records: "list[McpServerRecord]") -> None:
        write_json_atomic(self._path, [record.to_dict() for record in records])

    def add(self, record: McpServerRecord, *, force: bool = False) -> McpServerRecord:
        """Store one registration; a duplicate server_id refuses unless force=True.

        A forced overwrite keeps the new record wholesale — new command, new
        fingerprint, new allowed subset, nothing of the old reviewed shape —
        and freezes it: needs_review=True, because nobody has reviewed this
        shape yet. Clearing the freeze is `loom mcp review`'s job.
        """
        with self._lock:
            records = self._load()
            duplicate = any(existing.server_id == record.server_id for existing in records)
            if duplicate and not force:
                raise McpError(f"mcp server {record.server_id!r} is already registered; re-register with --force")
            stored = replace(record, needs_review=True, reviewed_at=None) if duplicate else record
            if duplicate:
                records = [stored if existing.server_id == record.server_id else existing for existing in records]
            else:
                records.append(stored)
            self._write(records)
        return stored

    def get(self, server_id: str) -> McpServerRecord:
        for record in self._load():
            if record.server_id == server_id:
                return record
        raise KeyError(f"no such mcp server: {server_id!r}")

    def list(self) -> "list[McpServerRecord]":
        return self._load()

    def set_status(
        self,
        server_id: str,
        *,
        needs_review: bool | None = None,
        enabled: bool | None = None,
        schema_fingerprint: str | None = None,
        reviewed_at: str | None = None,
    ) -> McpServerRecord:
        """The only mutation path for an existing record; returns the stored record.

        `allowed_tools` and `command` are deliberately absent: the allowed
        subset changes only through a forced re-registration, which freezes
        the record until a human re-reviews.
        """
        with self._lock:
            records = self._load()
            for index, existing in enumerate(records):
                if existing.server_id != server_id:
                    continue
                updated = replace(
                    existing,
                    needs_review=existing.needs_review if needs_review is None else needs_review,
                    enabled=existing.enabled if enabled is None else enabled,
                    schema_fingerprint=(
                        existing.schema_fingerprint if schema_fingerprint is None else schema_fingerprint
                    ),
                    reviewed_at=existing.reviewed_at if reviewed_at is None else reviewed_at,
                )
                records[index] = updated
                self._write(records)
                return updated
        raise KeyError(f"no such mcp server: {server_id!r}")
