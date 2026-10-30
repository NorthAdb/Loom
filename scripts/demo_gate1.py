"""Gate 1 demo: harmless tool passes, side-effect tool stops at the gate, reject does nothing.

Run with `.venv/Scripts/python scripts/demo_gate1.py`. With LLM_API_KEY set it uses
the real chat model; without one it runs a scripted fake and says so.
"""

import json
import sys
import tempfile
import uuid
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.types import Command

from loom.config import Settings
from loom.graph import build_graph
from loom.llm import make_model


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages in order; ignores bound tools."""

    def bind_tools(self, tools, **kwargs):
        return self


def tool_call_msg(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def audit_records(data_dir: Path) -> list[dict]:
    path = next((data_dir / "audit").glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def config_for(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def scenario_harmless(data_dir: Path, model) -> str:
    scripted = ScriptedChatModel(
        messages=iter([
            tool_call_msg("get_time", {}, "call_a1"),
            AIMessage(content="The tool result above holds the current local time."),
        ])
    )
    graph = build_graph(model or scripted, Settings(loom_env="dev", loom_data_dir=data_dir))
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "what time is it?"}], "run_id": uuid.uuid4().hex},
        config_for("demo-a"),
    )

    no_interrupt = "__interrupt__" not in result
    answered = bool(result["messages"][-1].content)
    no_audit = not (data_dir / "audit").exists()
    if no_interrupt and answered and no_audit:
        return "PASS"
    return f"FAIL (no_interrupt={no_interrupt}, answered={answered}, no_audit={no_audit})"


def scenario_approve(data_dir: Path, model) -> str:
    scripted = ScriptedChatModel(
        messages=iter([
            tool_call_msg("propose_sandbox_note", {"filename": "demo.txt", "content": "approved note"}, "call_b1"),
            AIMessage(content="Note written to the sandbox."),
        ])
    )
    graph = build_graph(model or scripted, Settings(loom_env="dev", loom_data_dir=data_dir))
    config = config_for("demo-b")

    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "save a demo note"}], "run_id": uuid.uuid4().hex}, config
    )
    held = "__interrupt__" in paused and not (data_dir / "notes" / "demo.txt").exists()
    result = graph.invoke(Command(resume={"decision": "approve"}), config)

    note = data_dir / "notes" / "demo.txt"
    written = note.exists() and bool(note.read_text(encoding="utf-8"))
    decisions = [r["decision"] for r in audit_records(data_dir)]
    audited = decisions == ["approved", "executed"]
    if held and written and audited and bool(result["messages"][-1].content):
        return "PASS"
    return f"FAIL (held_before_approval={held}, written={written}, audit={decisions})"


def scenario_reject(data_dir: Path, model) -> str:
    scripted = ScriptedChatModel(
        messages=iter([tool_call_msg("propose_sandbox_note", {"filename": "demo.txt", "content": "nope"}, "call_c1")])
    )
    graph = build_graph(model or scripted, Settings(loom_env="dev", loom_data_dir=data_dir))
    config = config_for("demo-c")

    graph.invoke({"messages": [{"role": "user", "content": "save a demo note"}], "run_id": uuid.uuid4().hex}, config)
    result = graph.invoke(Command(resume={"decision": "reject"}), config)

    no_file = not (data_dir / "notes" / "demo.txt").exists()
    decisions = [r["decision"] for r in audit_records(data_dir)]
    audited = decisions == ["rejected"]
    rejected_msg = "rejected" in result["messages"][-1].content
    if no_file and audited and rejected_msg:
        return "PASS"
    return f"FAIL (no_file={no_file}, audit={decisions}, rejected_msg={rejected_msg})"


def main() -> int:
    settings = Settings(loom_env="dev")
    if settings.llm_api_key:
        model = make_model(settings)
        print("LLM_API_KEY set; using the real chat model")
    else:
        model = None
        print("LLM_API_KEY not set; running the scripted fake model")

    root = Path(tempfile.mkdtemp(prefix="loom-gate1-"))
    results = []
    for label, name, fn in (
        ("a", "harmless tool answers without any interrupt", scenario_harmless),
        ("b", "side-effect tool approve: note written, audit approved+executed", scenario_approve),
        ("c", "side-effect tool reject: nothing written, audit rejected only", scenario_reject),
    ):
        data_dir = root / label
        outcome = fn(data_dir, model)
        results.append(outcome.startswith("PASS"))
        print(f"{outcome} ({label}) {name}")

    print()
    print("audit files:")
    for label in ("a", "b", "c"):
        for path in sorted((root / label / "audit").glob("*.jsonl")):
            print(f"  {path}")
            for line in path.read_text(encoding="utf-8").splitlines():
                print(f"    {line}")

    summary = "all scenarios passed" if all(results) else "SCENARIOS FAILED"
    print(f"\nGate 1 demo: {len(results)} scenarios, {sum(results)} passed - {summary}")
    print(f"data dir: {root}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
