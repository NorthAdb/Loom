"""The local draft store behind `draft_create_local`: drafts that are never sent.

One JSON file at `<data dir>/drafts.json`. This is the Gate 7 flagship side
effect: a triage turn writes a draft for human review, and nothing else. A
draft has no send control, no send tool exists in the registry, and no
address on the record changes that.
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from loom.ops.routines import now_local, write_json_atomic


@dataclass(frozen=True)
class LocalDraft:
    """One draft awaiting human review; local only, never sent."""

    draft_id: str
    to: str
    subject: str
    body: str
    status: str  # always "local_only"; the field exists so a viewer can assert it
    created_at: str


class DraftStore:
    """CRUD over `<data dir>/drafts.json`; writes serialized per process."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "drafts.json"
        self._lock = threading.Lock()

    def _load(self) -> list[LocalDraft]:
        if not self._path.exists():
            return []
        items = json.loads(self._path.read_text(encoding="utf-8"))
        return [LocalDraft(**item) for item in items]

    def _write(self, drafts: list[LocalDraft]) -> None:
        write_json_atomic(self._path, [asdict(draft) for draft in drafts])

    def add(self, to: str, subject: str, body: str) -> LocalDraft:
        draft = LocalDraft(
            draft_id=uuid.uuid4().hex,
            to=to,
            subject=subject,
            body=body,
            status="local_only",
            created_at=now_local(),
        )
        with self._lock:
            drafts = self._load()
            drafts.append(draft)
            self._write(drafts)
        return draft

    def list(self) -> list[LocalDraft]:
        """Drafts, oldest first."""
        return self._load()

    def get(self, draft_id: str) -> LocalDraft:
        for draft in self._load():
            if draft.draft_id == draft_id:
                return draft
        raise KeyError(f"no such draft: {draft_id!r}")
