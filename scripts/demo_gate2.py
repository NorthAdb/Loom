"""Gate 2 demo: jailed file tools tidy a messy sandbox, and a rejected delete touches nothing.

Run with `.venv/Scripts/python scripts/demo_gate2.py`. No Docker and no API key needed:
the scripted model drives the same LangGraph loop and approval gate that chat uses,
over the local jailed transport.
"""

import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from loom.config import Settings
from loom.graph import build_graph

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sandbox_messy"

MESSY = [
    "report.txt", "notes.md", "data.csv", "log.txt", "photo.jpg",
    "image.png", "song.mp3", "scan.pdf", "script.py", "archive.zip",
]


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages in order; ignores bound tools."""

    def bind_tools(self, tools, **kwargs):
        return self


def tool_call_msg(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def audit_records(data_dir: Path) -> list[dict]:
    path = next((data_dir / "audit").glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def print_tree(root: Path, label: str) -> None:
    print(f"{label} ({root})")
    def walk(directory: Path, indent: str) -> None:
        for entry in sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name)):
            print(f"{indent}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                walk(entry, indent + "  ")
    walk(root, "  ")


def config_for(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def run_to_completion(graph, config: dict, decision: str) -> dict:
    result = graph.invoke(
        {"messages": [HumanMessage(content="tidy the sandbox")], "run_id": uuid.uuid4().hex}, config
    )
    while "__interrupt__" in result:
        result = graph.invoke(Command(resume={"decision": decision}), config)
    return result


def scenario_tidy(base: Path) -> str:
    data_dir = base / "data"
    sandbox_root = base / "sandbox"
    shutil.copytree(FIXTURES, sandbox_root)
    print_tree(sandbox_root, "BEFORE tidy:")

    scripted = ScriptedChatModel(
        messages=iter(
            [
                tool_call_msg("move_file", {"src": name, "dst": f"sorted/{Path(name).suffix.lstrip('.')}/{name}"}, f"call_{i}")
                for i, name in enumerate(MESSY)
            ]
            + [AIMessage(content="All files are sorted by extension.")]
        )
    )
    graph = build_graph(scripted, Settings(loom_env="dev", loom_data_dir=data_dir, loom_sandbox_root=sandbox_root))
    run_to_completion(graph, config_for("demo-tidy"), "approve")

    print_tree(sandbox_root, "AFTER tidy:")

    placed = all(
        (sandbox_root / "sorted" / Path(name).suffix.lstrip(".") / name).is_file() for name in MESSY
    )
    contents_kept = all(
        (sandbox_root / "sorted" / Path(name).suffix.lstrip(".") / name).read_text(encoding="utf-8")
        == (FIXTURES / name).read_text(encoding="utf-8")
        for name in MESSY
    )
    no_leftovers = not any(entry.is_file() for entry in sandbox_root.iterdir())
    decisions = [record["decision"] for record in audit_records(data_dir)]
    audited = decisions == ["approved", "executed"] * len(MESSY)
    if placed and contents_kept and no_leftovers and audited:
        return "PASS"
    return f"FAIL (placed={placed}, contents_kept={contents_kept}, no_leftovers={no_leftovers}, audit={decisions})"


def scenario_delete_rejected(base: Path) -> str:
    data_dir = base / "data"
    sandbox_root = base / "sandbox"
    sandbox_root.mkdir(parents=True)
    keeper = sandbox_root / "keep_me.txt"
    keeper.write_text("do not delete me", encoding="utf-8")

    scripted = ScriptedChatModel(
        messages=iter([tool_call_msg("delete_file", {"path": "keep_me.txt"}, "call_reject")])
    )
    graph = build_graph(scripted, Settings(loom_env="dev", loom_data_dir=data_dir, loom_sandbox_root=sandbox_root))
    config = config_for("demo-reject")

    graph.invoke({"messages": [HumanMessage(content="delete keep_me.txt")], "run_id": uuid.uuid4().hex}, config)
    result = graph.invoke(Command(resume={"decision": "reject"}), config)

    still_there = keeper.exists() and keeper.read_text(encoding="utf-8") == "do not delete me"
    decisions = [record["decision"] for record in audit_records(data_dir)]
    audited = decisions == ["rejected"]
    rejected_msg = "rejected" in result["messages"][-1].content
    if still_there and audited and rejected_msg:
        return "PASS"
    return f"FAIL (still_there={still_there}, audit={decisions}, rejected_msg={rejected_msg})"


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="loom-gate2-"))
    results = []
    for label, name, fn in (
        ("a", "ten messy files tidied into sorted/<ext>/, each move approved and audited", scenario_tidy),
        ("b", "delete_file rejected: file untouched, audit has rejected and no executed", scenario_delete_rejected),
    ):
        outcome = fn(root / label)
        results.append(outcome.startswith("PASS"))
        print(f"{outcome} ({label}) {name}")

    print()
    print("audit files:")
    for label in ("a", "b"):
        for path in sorted((root / label / "data" / "audit").glob("*.jsonl")):
            print(f"  {path}")
            for line in path.read_text(encoding="utf-8").splitlines():
                print(f"    {line}")

    summary = "all scenarios passed" if all(results) else "SCENARIOS FAILED"
    print(f"\nGate 2 demo: {len(results)} scenarios, {sum(results)} passed - {summary}")
    print(f"data dir: {root}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
