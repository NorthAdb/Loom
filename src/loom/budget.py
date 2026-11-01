"""Daily token budget: the spend ledger, the brake guard, and token estimates.

The brake sits at the graph's callers (chat REPL, routine runner), never in the
graph itself: a refused run is never invoked.
"""

import json
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage

from loom.audit import AuditLog
from loom.config import Settings

UNLIMITED_LINE = "daily budget: unlimited"


def estimate_tokens(messages: Iterable[BaseMessage]) -> int:
    """Sum message tokens: API usage numbers when present, ~4 chars per token otherwise."""
    total = 0
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total += input_tokens + output_tokens
        else:
            total += len(str(message.content)) // 4 + 1
    return total


def turn_tokens(messages: list[BaseMessage]) -> int:
    """Estimate the tokens one turn added: everything from the turn's input message on.

    The turn's input HumanMessage is the last HumanMessage in the result state,
    so the slice covers it plus every AI/Tool message the graph appended this
    turn, including approval-resume steps.
    """
    last_input = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
    return estimate_tokens(messages[last_input:])


class SpendLedger:
    """Appends one JSON line per run to <data dir>/spend/YYYY-MM-DD.jsonl (local date)."""

    def __init__(self, data_dir: Path, today: Callable[[], date] | None = None) -> None:
        self._dir = Path(data_dir) / "spend"
        self._today = today or date.today

    def record(self, kind: str, run_id: str, tokens: int) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(), "run_id": run_id, "kind": kind, "tokens": tokens}
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{self._today().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def today_tokens(self) -> int:
        return sum(entry["tokens"] for entry in self._read(self._today().isoformat()))

    def total_tokens(self) -> int:
        return sum(entry["tokens"] for entry in self._read(None))

    def runs_today(self) -> dict[str, int]:
        return self._count_kinds(self._read(self._today().isoformat()))

    def runs_total(self) -> dict[str, int]:
        return self._count_kinds(self._read(None))

    def _read(self, day: str | None) -> list[dict]:
        """Entries from one day's file, or every file; unreadable files and lines are skipped."""
        paths = [self._dir / f"{day}.jsonl"] if day else sorted(self._dir.glob("*.jsonl"))
        entries = []
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and isinstance(entry.get("tokens"), int):
                    entries.append(entry)
        return entries

    @staticmethod
    def _count_kinds(entries: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in entries:
            kind = str(entry.get("kind", "unknown"))
            counts[kind] = counts.get(kind, 0) + 1
        return counts


class BudgetGuard:
    """Refuses runs once today's spend reaches the budget; 0 or negative means unlimited.

    Audit rejection keys avoid the word "token": the audit redactor masks values
    of any key that looks secret-bearing, and these values are counts to keep.
    """

    def __init__(self, budget_tokens: int, ledger: SpendLedger, audit: AuditLog) -> None:
        self._budget_tokens = budget_tokens
        self._ledger = ledger
        self._audit = audit

    def allow(self, kind: str, run_id: str) -> bool:
        """True when the run may start; a refusal also lands one audit record."""
        if self._budget_tokens <= 0:
            return True
        used = self._ledger.today_tokens()
        if used < self._budget_tokens:
            return True
        self._audit.append(
            run_id=run_id,
            actor="loom",
            tool=kind,
            args={"reason": "daily budget exceeded", "today": used, "budget": self._budget_tokens},
            decision="rejected",
        )
        return False

    def record(self, kind: str, run_id: str, tokens: int) -> None:
        self._ledger.record(kind, run_id, tokens)

    def limit_line(self) -> str:
        if self._budget_tokens <= 0:
            return UNLIMITED_LINE
        used = self._ledger.today_tokens()
        return f"daily budget: {self._budget_tokens} tokens, {used} used, {self._budget_tokens - used} left"

    def refusal_line(self) -> str:
        """The one-line refusal a caller emits when allow() said no."""
        return f"daily budget exceeded: {self._ledger.today_tokens()} of {self._budget_tokens} tokens used today; not run"


def guard_from_settings(settings: Settings, data_dir: Path) -> BudgetGuard | None:
    """The guard for these settings, or None when unlimited so callers write no ledger at all."""
    budget = settings.daily_budget_tokens
    if budget <= 0:
        return None
    return BudgetGuard(budget, SpendLedger(data_dir), AuditLog(data_dir))
