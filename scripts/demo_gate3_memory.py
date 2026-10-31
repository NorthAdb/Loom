"""Gate 3 demo (Phase 4): the memory baseline across sessions, without any API key.

Run with `.venv/Scripts/python scripts/demo_gate3_memory.py`. A fake semantic
store rides the same seams the real Mem0 adapter uses (build_graph's `memory`
parameter for the graph, `loom.cli.build_semantic_memory` for the CLI), and a
scripted model drives the same LangGraph loop chat uses. The facts table is the
real sqlite `FactsStore` reached through the real `loom memory` CLI commands.
"""

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage

import loom.cli
from loom.config import Settings, get_settings
from loom.graph import build_graph


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages in order; ignores bound tools."""

    def bind_tools(self, tools, **kwargs):
        return self


class RecordingModel:
    """Wraps a scripted model and records every message list invoke received."""

    def __init__(self, scripted):
        self.inner = ScriptedChatModel(messages=iter(scripted))
        self.seen = []

    def bind_tools(self, tools, **kwargs):
        self.inner.bind_tools(tools, **kwargs)
        return self

    def invoke(self, input, config=None, **kwargs):
        self.seen.append(list(input))
        return self.inner.invoke(input, config, **kwargs)


class FakeSemantic:
    """Semantic memory double over a dict, standing in for the Mem0 adapter."""

    def __init__(self, store):
        self.store = store

    def add(self, texts):
        for text in texts:
            self.store[text] = text

    def search(self, query, limit=3):
        return list(self.store.values())[:limit]


def cli(argv: list) -> tuple[int, str]:
    """Run one real `loom` CLI command, returning (exit code, stdout)."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = loom.cli.main(argv)
    return code, buffer.getvalue()


def new_graph(data_dir: Path, store: dict, scripted: list[str]):
    """Build one fresh graph instance the way a new chat session would."""
    settings = Settings(loom_env="dev", loom_data_dir=data_dir, loom_sandbox_root=data_dir.parent / "sandbox")
    model = RecordingModel([AIMessage(content=text) for text in scripted])
    return build_graph(model, settings, memory=FakeSemantic(store)), model


def chat_turn(data_dir: Path, store: dict, user_text: str, scripted: str) -> tuple[str, str]:
    """Run one chat turn on a fresh graph instance; returns (injected note, reply)."""
    graph, model = new_graph(data_dir, store, [scripted])
    result = graph.invoke(
        {"messages": [{"role": "user", "content": user_text}], "run_id": "demo"},
        {"configurable": {"thread_id": "demo-thread"}},
    )
    system = model.seen[0][0]
    assert isinstance(system, SystemMessage)
    return system.content, str(result["messages"][-1].content)


def scenario_fact_reaches_a_new_session(data_dir: Path, store: dict) -> str:
    code, out = cli(["memory", "facts", "set", "reply_language", "中文"])
    print(f"  loom memory facts set reply_language 中文 -> exit {code}: {out.strip()}")
    if code != 0:
        return "FAIL (facts set failed)"

    note, answer = chat_turn(data_dir, store, "用哪种语言回复？", "好的，用中文回复")

    if "reply_language: 中文" in note and "权威" in note and answer == "好的，用中文回复":
        return "PASS"
    return f"FAIL (note={note!r}, answer={answer!r})"


def scenario_explicit_message_is_memorized_and_searchable(data_dir: Path, store: dict) -> str:
    chat_turn(data_dir, store, "记住 我喜欢简洁回复", "好的，记下了")
    if "记住 我喜欢简洁回复" not in store:
        return f"FAIL (store={list(store)})"

    original = loom.cli.build_semantic_memory
    loom.cli.build_semantic_memory = lambda settings: FakeSemantic(store)
    try:
        code, out = cli(["memory", "search", "回复偏好"])
    finally:
        loom.cli.build_semantic_memory = original
    print(f"  loom memory search 回复偏好 -> exit {code}: {out.strip()}")
    if code == 0 and "我喜欢简洁回复" in out:
        return "PASS"
    return f"FAIL (search out={out!r})"


def scenario_fact_correction_changes_the_note(data_dir: Path, store: dict) -> str:
    code, out = cli(["memory", "facts", "set", "reply_language", "English"])
    print(f"  loom memory facts set reply_language English -> exit {code}: {out.strip()}")

    note, _answer = chat_turn(data_dir, store, "用哪种语言回复？", "好的，用英文回复")

    if code == 0 and "reply_language: English" in note and "中文" not in note:
        return "PASS"
    return f"FAIL (note={note!r})"


def scenario_deleted_fact_leaves_the_note(data_dir: Path, store: dict) -> str:
    code, out = cli(["memory", "facts", "del", "reply_language"])
    print(f"  loom memory facts del reply_language -> exit {code}: {out.strip()}")

    note, _answer = chat_turn(data_dir, store, "在吗", "在的")

    if code == 0 and "reply_language" not in note:
        return "PASS"
    return f"FAIL (note={note!r})"


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="loom-gate3-"))
    data_dir = root / "data"
    data_dir.mkdir()
    os.environ["LOOM_ENV"] = "dev"
    os.environ["LOOM_DATA_DIR"] = str(data_dir)
    os.environ["LOOM_MEMORY_ENABLED"] = "false"
    get_settings.cache_clear()
    store: dict = {}

    results = []
    for label, name, fn in (
        ("a", "fact set through the CLI reaches a brand-new graph session as an authoritative note", scenario_fact_reaches_a_new_session),
        ("b", "an explicit 记住 message is written back and found by loom memory search", scenario_explicit_message_is_memorized_and_searchable),
        ("c", "correcting the fact through the CLI changes the note in the next session", scenario_fact_correction_changes_the_note),
        ("d", "deleting the fact removes it from the note", scenario_deleted_fact_leaves_the_note),
    ):
        outcome = fn(data_dir, store)
        results.append(outcome.startswith("PASS"))
        print(f"{outcome} ({label}) {name}")

    summary = "all scenarios passed" if all(results) else "SCENARIOS FAILED"
    print(f"\nGate 3 memory demo: {len(results)} scenarios, {sum(results)} passed - {summary}")
    print(f"data dir: {root}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
