"""The unattended approval queue behind `loom approvals`.

When a routine run reaches the approval gate, the runner persists a
PendingApproval and stops; nothing executes until a human resolves the record
with `loom approvals approve|reject`. Records live in
`<data dir>/approvals/pending.json`.
"""

import json
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from loom.routines import write_json_atomic

PENDING = "pending"
COMPLETED = "completed"  # every resolved record lands here; the approve/reject decision lives in the audit log


@dataclass(frozen=True)
class PendingApproval:
    """One paused routine run waiting for a human decision."""

    pending_id: str
    thread_id: str
    routine_id: str
    tools: list  # interrupt payload entries: {"name": ..., "args": ...}
    reason: str
    created_at: str
    status: str  # "pending" | "completed"; the approve/reject decision lives in the audit log


class PendingStore:
    """CRUD over `<data dir>/approvals/pending.json`; no audit here, the gate node audits decisions.

    Writes are serialized per process; run one worker per data dir (see README).
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "approvals" / "pending.json"
        self._lock = threading.Lock()

    def _load(self) -> list[PendingApproval]:
        if not self._path.exists():
            return []
        items = json.loads(self._path.read_text(encoding="utf-8"))
        return [PendingApproval(**item) for item in items]

    def add(self, record: PendingApproval) -> PendingApproval:
        """Persist one new pending record and return it."""
        with self._lock:
            records = self._load()
            records.append(record)
            self._write(records)
        return record

    def list_pending(self) -> list[PendingApproval]:
        """Return records still waiting for a decision, in creation order."""
        return [record for record in self._load() if record.status == PENDING]

    def get(self, pending_id: str) -> PendingApproval:
        for record in self._load():
            if record.pending_id == pending_id:
                return record
        raise KeyError(f"no such pending approval: {pending_id!r}")

    def set_status(self, pending_id: str, status: str, tools: list | None = None) -> PendingApproval:
        """Move one record to a new status; `tools` refreshes the payload when a resumed run re-pends."""
        with self._lock:
            records = self._load()
            for index, record in enumerate(records):
                if record.pending_id == pending_id:
                    updated = replace(record, status=status, tools=record.tools if tools is None else tools)
                    records[index] = updated
                    self._write(records)
                    return updated
        raise KeyError(f"no such pending approval: {pending_id!r}")

    def _write(self, records: list[PendingApproval]) -> None:
        write_json_atomic(self._path, [asdict(record) for record in records])
