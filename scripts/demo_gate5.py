"""Gate 5 demo (Phase 7): the budget brake, backup/restore, and the eval summary.

Run with `.venv/Scripts/python scripts/demo_gate5.py`. Keyless: a scripted
model drives the real chat REPL loop (`loom.cli.chat_session`) against the real
graph, the backup scenario uses the real create_backup/restore_backup over a
temp data dir, and scenario (c) runs the repository's eval cases through the
real harness. Nothing is written outside temp dirs.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from loom import eval as loom_eval
from loom.audit import AuditLog
from loom.backup import BACKUP_ROOTS, create_backup, restore_backup
from loom.budget import BudgetGuard, SpendLedger
from loom.cli import chat_session
from loom.config import Settings
from loom.graph import build_graph
from loom.memory import FactsStore
from loom.routines import Routine, RoutineStore

BUDGET_REJECTED_REASON = "daily budget exceeded"


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages in order; ignores bound tools."""

    def bind_tools(self, tools, **kwargs):
        return self


def settings_for(data_dir: Path, **overrides) -> Settings:
    return Settings(
        loom_env="dev",
        loom_data_dir=data_dir,
        loom_sandbox_root=data_dir.parent / "sandbox",
        loom_sandbox_url="",
        memory_enabled=False,
        ical_url="",
        imap_host="",
        browser_allowed_hosts="",
        **overrides,
    )


def budget_rejection_row(data_dir: Path) -> dict | None:
    """The first audit row carrying the daily-budget refusal, or None."""
    for path in sorted((data_dir / "audit").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            try:
                args = json.loads(record.get("args", ""))
            except ValueError:
                continue
            if record.get("decision") == "rejected" and isinstance(args, dict) and args.get("reason") == BUDGET_REJECTED_REASON:
                return record
    return None


def scenario_budget_refusal(root: Path) -> str:
    data_dir = root / "budget" / "data"
    data_dir.mkdir(parents=True)
    settings = settings_for(data_dir, daily_budget_tokens=1)
    model = ScriptedChatModel(messages=iter([AIMessage(content="hello")]))
    graph = build_graph(model, settings)
    budget = BudgetGuard(settings.daily_budget_tokens, SpendLedger(data_dir), AuditLog(data_dir))

    # The brake allows a turn while today's spend is under the budget, so the
    # first "hi" runs and records its tokens; the second "hi" is refused.
    typed = iter(["hi", "hi again", "quit"])
    emitted: list[str] = []
    chat_session(graph, "budget-thread", lambda prompt: next(typed), emitted.append, budget=budget)

    refusal = next((line for line in emitted if BUDGET_REJECTED_REASON in line), None)
    if refusal is None:
        return f"FAIL (no refusal line in the emitted output: {emitted!r})"
    print(f"  chat emitted: {refusal}")
    row = budget_rejection_row(data_dir)
    if row is None:
        return "FAIL (no audit row for the budget refusal)"
    print(f"  audit row: {json.dumps(row, ensure_ascii=False)}")
    return "PASS"


def scenario_backup_restore(root: Path) -> str:
    data_dir = root / "backup" / "data"
    data_dir.mkdir(parents=True)
    FactsStore(data_dir).upsert("reply_language", "中文")
    RoutineStore(data_dir).add(Routine(id="daily_tidy", name="Tidy the sandbox", cron="30 9 * * *", prompt="Tidy the sandbox."))

    def roots_present() -> list[str]:
        return [name for name in BACKUP_ROOTS if (data_dir / name).exists()]

    print(f"  before backup: {', '.join(roots_present())}")
    archive = create_backup(data_dir, root / "backup" / "archives")
    print(f"  backup: {archive.name}")
    for name in roots_present():
        target = data_dir / name
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    # Reading facts here would recreate facts.db (sqlite connects on demand),
    # so the deletion proof is the root listing, not a store read.
    print(f"  after delete: roots present -> {roots_present() or '(none)'}")

    restore_backup(archive, data_dir, overwrite=False)
    fact = FactsStore(data_dir).get("reply_language")
    routine = RoutineStore(data_dir).get("daily_tidy")
    if fact is None or fact.value != "中文":
        return f"FAIL (the fact did not survive the round trip: {fact!r})"
    if routine.id != "daily_tidy" or routine.cron != "30 9 * * *":
        return f"FAIL (the routine did not survive the round trip: {routine!r})"
    print(f"  after restore: facts.get('reply_language') -> {fact.value!r}; routine {routine.id} cron='{routine.cron}'")
    return "PASS"


def scenario_eval_summary() -> str:
    cases_dir = Path(__file__).resolve().parents[1] / "tests" / "eval" / "cases"
    if not cases_dir.is_dir():
        return f"FAIL (no eval cases at {cases_dir}; run from the repository root)"
    summary = loom_eval.run_cases(cases_dir)
    print("  " + loom_eval.format_summary(summary).replace("\n", "\n  "))
    if summary.rate >= 0.8 and summary.canary_ok:
        return "PASS"
    return "FAIL (rate below 80% or the canary judged green)"


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="loom-gate5-"))
    results = []
    for label, name, fn in (
        ("a", "the budget brake refuses a chat turn once today's spend reaches the budget", lambda: scenario_budget_refusal(root)),
        ("b", "create_backup + restore_backup round-trips the facts table and the routines", lambda: scenario_backup_restore(root)),
        ("c", "the eval harness runs the repository's cases green with the canary red", scenario_eval_summary),
    ):
        outcome = fn()
        results.append(outcome.startswith("PASS"))
        print(f"{outcome} ({label}) {name}")

    summary = "all scenarios passed" if all(results) else "SCENARIOS FAILED"
    print(f"\nGate 5 demo: {len(results)} scenarios, {sum(results)} passed - {summary}")
    print(f"data dir: {root}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
