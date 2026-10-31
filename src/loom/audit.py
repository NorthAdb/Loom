"""Append-only JSONL audit log; one line per gate decision or tool execution."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loom.logging import is_sensitive

_VALUE_CAP = 200


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Mask values whose key looks secret-bearing; keys stay visible."""
    return {name: ("***" if is_sensitive(name) else value) for name, value in args.items()}


def summarize_args(args: dict[str, Any], cap: int = _VALUE_CAP) -> str:
    """Serialize args for records and interrupt payloads: redacted, string values capped.

    Audit records keep the default cap; the gate's interrupt payload passes a
    larger one so an approval can show a full draft text.
    """
    redacted = redact_args(args)
    capped = {name: (value[:cap] if isinstance(value, str) else value) for name, value in redacted.items()}
    return json.dumps(capped, ensure_ascii=False)


class AuditLog:
    """Appends records to <data dir>/audit/YYYYMMDD.jsonl (local date)."""

    def __init__(self, data_dir: Path) -> None:
        self._dir = Path(data_dir) / "audit"

    def append(self, *, run_id: str, actor: str, tool: str, args: dict[str, Any], decision: str) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "actor": actor,
            "tool": tool,
            "args": summarize_args(args),
            "decision": decision,
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{datetime.now().strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
