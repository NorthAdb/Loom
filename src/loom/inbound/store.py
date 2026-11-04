"""The inbox: accepted inbound messages with the task and scope they produced.

One JSON file at `<data dir>/inbound/inbox.json`, written atomically. The
record keeps the message exactly as received (it is data about untrusted
content, so the subject and body are stored as-is for the console's Inbox
view) alongside the task id and the scope id the accept step issued.
"""

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from loom.inbound.types import InboundMessage
from loom.ops.routines import write_json_atomic


@dataclass(frozen=True)
class InboxRecord:
    """One accepted message plus the work it created."""

    message: InboundMessage
    task_id: str
    scope_id: str | None
    accepted_at: str

    def to_dict(self) -> dict:
        return {
            "message": {
                "message_id": self.message.message_id,
                "channel": self.message.channel,
                "from_header": self.message.from_header,
                "subject": self.message.subject,
                "body": self.message.body,
                "received_at": self.message.received_at,
                "authenticated": self.message.authenticated,
            },
            "task_id": self.task_id,
            "scope_id": self.scope_id,
            "accepted_at": self.accepted_at,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "InboxRecord":
        raw = payload["message"]
        return cls(
            message=InboundMessage(
                message_id=raw["message_id"],
                channel=raw["channel"],
                from_header=raw["from_header"],
                subject=raw["subject"],
                body=raw["body"],
                received_at=raw["received_at"],
                authenticated=raw["authenticated"],
            ),
            task_id=payload["task_id"],
            scope_id=payload["scope_id"],
            accepted_at=payload["accepted_at"],
        )


class InboxStore:
    """CRUD over `<data dir>/inbound/inbox.json`; writes serialized per process."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "inbound" / "inbox.json"
        self._lock = threading.Lock()

    def _load(self) -> list[InboxRecord]:
        if not self._path.exists():
            return []
        items = json.loads(self._path.read_text(encoding="utf-8"))
        return [InboxRecord.from_dict(item) for item in items]

    def _write(self, records: list[InboxRecord]) -> None:
        write_json_atomic(self._path, [record.to_dict() for record in records])

    def add(self, message: InboundMessage, *, task_id: str, scope_id: str | None, accepted_at: str | None = None) -> InboxRecord:
        with self._lock:
            records = self._load()
            if any(record.message.message_id == message.message_id for record in records):
                raise ValueError(f"inbox already holds message {message.message_id!r}")
            record = InboxRecord(
                message=message,
                task_id=task_id,
                scope_id=scope_id,
                accepted_at=accepted_at or message.received_at,
            )
            records.append(record)
            self._write(records)
        return record

    def list(self) -> list[InboxRecord]:
        """Accepted messages, oldest first."""
        return self._load()

    def get(self, message_id: str) -> InboxRecord:
        for record in self._load():
            if record.message.message_id == message_id:
                return record
        raise KeyError(f"no such inbox message: {message_id!r}")
