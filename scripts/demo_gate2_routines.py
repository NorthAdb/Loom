"""Gate 2 routines demo: cron-defined routines run through the same graph, and a write routine waits for approval.

Run with `.venv/Scripts/python scripts/demo_gate2_routines.py`. No Docker, no API key, no
scheduler wait: the scripted model drives the shared LangGraph loop and approval gate that
chat and the worker both use, over the local jailed transport. The accelerated schedule
trigger itself is evidence in tests/test_scheduler.py.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

import loom.cli
import loom.graph
import loom.runtime
from loom.config import Settings
from loom.graph import build_graph
from loom.memory import NullSemanticMemory
from loom.pending import PendingStore
from loom.routines import Routine, RoutineStore
from loom.runner import run_routine

loom.graph.build_semantic_memory = lambda settings: NullSemanticMemory()

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sandbox_messy"

MESSY = sorted(entry.name for entry in FIXTURES.iterdir() if entry.is_file())


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages in order; ignores bound tools."""

    def bind_tools(self, tools, **kwargs):
        return self


def tool_call_msg(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def audit_records(data_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for path in sorted((data_dir / "audit").glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def print_tree(root: Path, label: str) -> None:
    print(f"{label} ({root})")
    def walk(directory: Path, indent: str) -> None:
        for entry in sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name)):
            print(f"{indent}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                walk(entry, indent + "  ")
    walk(root, "  ")


def scenario_stats(settings: Settings, store: RoutineStore) -> str:
    pending = PendingStore(settings.loom_data_dir)
    scripted = ScriptedChatModel(
        messages=iter(
            [
                tool_call_msg("list_dir", {}, "call_ls"),
                AIMessage(content=f"The sandbox holds {len(MESSY)} files: {', '.join(MESSY)}."),
            ]
        )
    )
    graph = build_graph(scripted, settings)
    outcome = run_routine(store.get("daily_sandbox_stats"), settings, store, pending, graph=graph)

    print(f"stats answer: {outcome.answer}")
    triggered = [record for record in audit_records(settings.loom_data_dir) if record["tool"] == "routine_trigger"]
    for record in triggered:
        print(f"  audit: {json.dumps(record, ensure_ascii=False)}")

    ok = (
        outcome.outcome == "completed"
        and len(triggered) == 1
        and outcome.thread_id.startswith("routine:daily_sandbox_stats:")
        and store.get("daily_sandbox_stats").last_status == "completed"
        and pending.list_pending() == []
    )
    return "PASS" if ok else f"FAIL (outcome={outcome.outcome}, triggered={len(triggered)})"


def scenario_tidy(settings: Settings, store: RoutineStore) -> str:
    data_dir = settings.loom_data_dir
    pending = PendingStore(data_dir)
    tidy_model = ScriptedChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "move_file",
                            "args": {"src": name, "dst": f"sorted/{Path(name).suffix.lstrip('.')}/{name}"},
                            "id": f"call_{i}",
                            "type": "tool_call",
                        }
                        for i, name in enumerate(MESSY)
                    ],
                ),
                AIMessage(content="All files are sorted by extension."),
            ]
        )
    )
    graph = build_graph(tidy_model, settings)
    outcome = run_routine(store.get("daily_tidy"), settings, store, pending, graph=graph)
    pending_id = outcome.pending_id
    print(f"tidy run outcome: {outcome.outcome}, pending_id: {pending_id}")
    record = pending.get(pending_id) if pending_id else None
    print(f"pending tools: {len(record.tools) if record else 0} x move_file, reason: {record.reason if record else '-'}")

    blocked = not any(sandbox_target(name, settings).exists() for name in MESSY)
    print("approvals list output:")
    assert loom.cli.main(["approvals", "list"]) == 0

    # The resumed run opens a fresh graph, so the patch must return the same tidy_model instance.
    loom.runtime.make_model = lambda settings: tidy_model
    approved = loom.cli.main(["approvals", "approve", pending_id]) == 0
    print(f"approve exit ok: {approved}")

    placed = all(sandbox_target(name, settings).is_file() for name in MESSY)
    contents_kept = all(
        sandbox_target(name, settings).read_text(encoding="utf-8") == (FIXTURES / name).read_text(encoding="utf-8")
        for name in MESSY
    )
    no_leftovers = not any(entry.is_file() for entry in settings.loom_sandbox_root.iterdir())
    moves = [rec for rec in audit_records(data_dir) if rec["tool"] == "move_file"]
    approved_count = sum(1 for rec in moves if rec["decision"] == "approved")
    executed_count = sum(1 for rec in moves if rec["decision"] == "executed")
    ok = (
        outcome.outcome == "pending"
        and blocked
        and record is not None and len(record.tools) == len(MESSY)
        and approved
        and placed
        and contents_kept
        and no_leftovers
        and approved_count == len(MESSY)
        and executed_count == len(MESSY)
    )
    if not ok:
        return (
            f"FAIL (outcome={outcome.outcome}, blocked={blocked}, approved={approved}, placed={placed}, "
            f"contents_kept={contents_kept}, no_leftovers={no_leftovers}, "
            f"audit approved={approved_count}, executed={executed_count})"
        )
    return "PASS"


def sandbox_target(name: str, settings: Settings) -> Path:
    return settings.loom_sandbox_root / "sorted" / Path(name).suffix.lstrip(".") / name


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="loom-gate2-routines-"))
    data_dir = root / "data"
    sandbox_root = root / "sandbox"
    shutil.copytree(FIXTURES, sandbox_root)
    # The approval CLI reads its settings from the environment, like any other loom process.
    os.environ["LOOM_ENV"] = "dev"
    os.environ["LOOM_DATA_DIR"] = str(data_dir)
    os.environ["LOOM_SANDBOX_ROOT"] = str(sandbox_root)
    os.environ.pop("LOOM_SANDBOX_URL", None)

    settings = Settings(loom_env="dev", loom_data_dir=data_dir, loom_sandbox_root=sandbox_root)
    store = RoutineStore(data_dir)
    store.add(
        Routine(
            id="daily_sandbox_stats",
            name="Daily sandbox stats",
            cron="30 8 * * *",
            prompt="List the sandbox and summarize how many files there are.",
        )
    )
    store.add(
        Routine(
            id="daily_tidy",
            name="Daily tidy",
            cron="0 9 * * *",
            prompt="Tidy the sandbox: move every file into sorted/<extension>/.",
        )
    )
    print(f"registered routines: {', '.join(r.id for r in store.load())}")
    print_tree(sandbox_root, "BEFORE routines:")

    results = []
    for label, name, fn in (
        ("a", "daily_sandbox_stats runs read-only through the shared graph, trigger audited", scenario_stats),
        ("b", "daily_tidy pends without writing; one approval moves all files and audits approved+executed", scenario_tidy),
    ):
        print()
        outcome = fn(settings, store)
        results.append(outcome.startswith("PASS"))
        print(f"{outcome} ({label}) {name}")

    print()
    print("sandbox after:")
    print_tree(sandbox_root, "AFTER routines:")
    print()
    print("audit files:")
    for record in audit_records(data_dir):
        print(f"  {json.dumps(record, ensure_ascii=False)}")

    summary = "all scenarios passed" if all(results) else "SCENARIOS FAILED"
    print(f"\nGate 2 routines demo: {len(results)} scenarios, {sum(results)} passed - {summary}")
    print(f"data dir: {root}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
