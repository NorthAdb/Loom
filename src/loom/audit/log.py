"""Append-only JSONL audit log; one line per gate decision or tool execution."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loom.core.logging import is_sensitive
from loom.core.secrets import known_secret_values, scrub_value

_VALUE_CAP = 200


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Mask values whose key looks secret-bearing; keys stay visible."""
    return {name: ("***" if is_sensitive(name) else value) for name, value in args.items()}


def summarize_args(args: dict[str, Any], cap: int | None = _VALUE_CAP) -> str:
    """Serialize args for records and interrupt payloads: redacted, string values capped.

    Audit records keep the default cap. The gate passes cap=None for mail tool
    args so an approval shows the full draft text (oversized drafts are refused
    before approval), and its larger payload cap for other tools.
    """
    redacted = redact_args(args)
    capped = {name: (value[:cap] if isinstance(value, str) else value) for name, value in redacted.items()}
    return json.dumps(capped, ensure_ascii=False)


class AuditLog:
    """Appends records to <data dir>/audit/YYYYMMDD.jsonl (local date).

    Args are scrubbed of known secret values before they are written: a
    secret-named key is masked by `redact_args`, and a known secret *value*
    appearing under any key (an MCP tool argument echoing the mail password,
    say) is masked here, at the persistence boundary, whatever the caller.
    `settings`, when supplied, contributes its mail password and LLM key to
    the scrub set; without it the set falls back to the environment those
    fields load from plus every registered server's credential.

    The scrub runs on the raw args BEFORE `summarize_args` truncates string
    values: scrubbing the summary instead would never match a secret longer
    than the summary cap, and its first `cap` characters would be written
    verbatim. The audit summary cap itself is unchanged, and approval
    payloads (built by callers with `cap=None`) are not affected.
    """

    def __init__(self, data_dir: Path, settings: Any = None) -> None:
        self._dir = Path(data_dir) / "audit"
        self._data_dir = Path(data_dir)
        self._settings = settings

    def append(
        self,
        *,
        run_id: str,
        actor: str,
        tool: str,
        args: dict[str, Any],
        decision: str,
        correlation_id: str | None = None,
    ) -> None:
        """Append one record; Gate 7 rows carry the scope's correlation_id.

        Old rows and callers without a scope stay exactly as they were: the
        field is simply absent when no correlation_id is given.
        """
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "actor": actor,
            "tool": tool,
            "args": summarize_args(scrub_value(args, known_secret_values(self._settings, self._data_dir))),
            "decision": decision,
        }
        if correlation_id is not None:
            record["correlation_id"] = correlation_id
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{datetime.now().strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
