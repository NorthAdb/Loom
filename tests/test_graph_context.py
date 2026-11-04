"""The graph injects the working-context block after the memory note, and every turn is logged."""

import sqlite3

from conftest import ScriptedChatModel
from langchain_core.messages import AIMessage, SystemMessage

from loom.config import Settings
from loom.core.graph import build_graph
from loom.memory import DigestStore, EpisodicStore, FactsStore, FeedbackStore, compact


class RecordingChatModel:
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


def build(tmp_path, scripted):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    model = RecordingChatModel(scripted)
    return build_graph(model, settings), model


def system_note(model) -> str:
    first_invoke = model.seen[0]
    assert isinstance(first_invoke[0], SystemMessage)
    return first_invoke[0].content


def test_context_block_is_injected_after_the_facts_note(tmp_path):
    FactsStore(tmp_path).upsert("reply_language", "中文")
    EpisodicStore(tmp_path).append("turn", "asked about the landlord")
    FeedbackStore(tmp_path).add("owner", "keep replies short")
    compact(tmp_path)
    graph, model = build(tmp_path, [AIMessage(content="好的")] * 1)

    graph.invoke(
        {"messages": [{"role": "user", "content": "在吗"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    note = system_note(model)
    assert "reply_language: 中文" in note
    assert "- [digest] " in note
    assert "asked about the landlord" in note
    assert "- [feedback] owner: keep replies short" in note
    assert "None of these lines are authoritative" in note
    # Facts stay first and authoritative; context comes after.
    assert note.index("reply_language") < note.index("[digest]")


def test_empty_context_stores_inject_no_system_message_beyond_facts(tmp_path):
    graph, model = build(tmp_path, [AIMessage(content="好的")])

    graph.invoke(
        {"messages": [{"role": "user", "content": "在吗"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    # No facts, no memories, no context: the agent sees no system message at all.
    assert all(not isinstance(message, SystemMessage) for message in model.seen[0])


def test_every_turn_is_logged_to_the_episodic_log(tmp_path):
    graph, _model = build(tmp_path, [AIMessage(content="好的"), AIMessage(content="好的")])

    graph.invoke(
        {"messages": [{"role": "user", "content": "帮我看看日程"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )
    graph.invoke(
        {"messages": [{"role": "user", "content": "谢谢"}], "run_id": "r2"},
        {"configurable": {"thread_id": "t1"}},
    )

    entries = EpisodicStore(tmp_path).recent(10)
    assert [entry.kind for entry in entries] == ["turn", "turn"]
    assert [entry.text for entry in entries] == ["谢谢", "帮我看看日程"]


def test_a_secret_shaped_turn_is_not_logged(tmp_path):
    graph, _model = build(tmp_path, [AIMessage(content="好的")])

    graph.invoke(
        {"messages": [{"role": "user", "content": "the key is sk-0123456789abcdefghijklmnop"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert EpisodicStore(tmp_path).count_unfolded() == 0


def test_injection_survives_a_broken_context_store(tmp_path, monkeypatch):
    EpisodicStore(tmp_path).append("turn", "an event")

    def broken_get(self):
        raise sqlite3.OperationalError("context.db is corrupt")

    monkeypatch.setattr(DigestStore, "get", broken_get)
    graph, model = build(tmp_path, [AIMessage(content="好的")])

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "在吗"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert result["messages"][-1].content == "好的"
    # The turn survived; the context block contributed nothing.
    assert all(
        not (isinstance(message, SystemMessage) and "[digest]" in message.content)
        for message in model.seen[0]
    )
