"""Routine definitions and the JSON store behind `loom routines`.

A Routine is one scheduled prompt. The store keeps them in
`<data dir>/routines.json`, validates at add time, and audits every
human-driven mutation. Status fields (`last_run_at` / `last_status`) are
bookkeeping written by the runner; they are not audit records.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.triggers.cron import CronTrigger

from loom.audit import AuditLog


@dataclass(frozen=True)
class Routine:
    """One scheduled prompt sent through the same compiled graph as chat."""

    id: str
    name: str
    cron: str  # 5-field crontab: minute hour day-of-month month day-of-week
    prompt: str
    enabled: bool = True
    last_run_at: str = ""
    last_status: str = ""


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write JSON to a temp file next to the target, then os.replace it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f"{path}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class RoutineStore:
    """CRUD over `<data dir>/routines.json`; add and remove write one audit record each."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "routines.json"
        self._audit = AuditLog(data_dir)

    def load(self) -> list[Routine]:
        """Return every stored routine; an absent file means an empty list."""
        if not self._path.exists():
            return []
        items = json.loads(self._path.read_text(encoding="utf-8"))
        return [Routine(**item) for item in items]

    def list(self) -> list[Routine]:
        """Alias for load(), matching the CLI verb."""
        return self.load()

    def get(self, routine_id: str) -> Routine:
        for routine in self.load():
            if routine.id == routine_id:
                return routine
        raise KeyError(f"no such routine: {routine_id!r}")

    def add(self, routine: Routine) -> Routine:
        """Validate and persist a new routine; rejects duplicate ids and unparseable crons."""
        if any(existing.id == routine.id for existing in self.load()):
            raise ValueError(f"routine id already exists: {routine.id!r}")
        try:
            CronTrigger.from_crontab(routine.cron)
        except ValueError as exc:
            raise ValueError(f"routine.cron is not a valid 5-field crontab: {routine.cron!r} ({exc})") from exc
        routines = self.load()
        routines.append(routine)
        self._write(routines)
        self._audit.append(
            run_id="-",
            actor="human",
            tool="routines",
            args={"action": "add", "id": routine.id, "cron": routine.cron},
            decision="executed",
        )
        return routine

    def remove(self, routine_id: str) -> Routine:
        """Delete one routine by id; unknown ids raise KeyError."""
        removed = self.get(routine_id)
        self._write([routine for routine in self.load() if routine.id != routine_id])
        self._audit.append(
            run_id="-",
            actor="human",
            tool="routines",
            args={"action": "remove", "id": routine_id, "cron": removed.cron},
            decision="executed",
        )
        return removed

    def set_status(self, routine_id: str, last_run_at: str, last_status: str) -> Routine:
        """Runner bookkeeping: record when the routine last fired and how it ended."""
        routines = self.load()
        for index, routine in enumerate(routines):
            if routine.id == routine_id:
                updated = replace(routine, last_run_at=last_run_at, last_status=last_status)
                routines[index] = updated
                self._write(routines)
                return updated
        raise KeyError(f"no such routine: {routine_id!r}")

    def _write(self, routines: list[Routine]) -> None:
        write_json_atomic(self._path, [asdict(routine) for routine in routines])


def now_local() -> str:
    """Local-time ISO stamp used for created_at and last_run_at fields."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
