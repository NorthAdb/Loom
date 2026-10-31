from conftest import ScriptedChatModel
from langchain_core.messages import AIMessage, SystemMessage

from loom.config import Settings
from loom.graph import MEMORY_REFUSAL, build_graph
from loom.memory import FactsStore


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


class FakeSemantic:
    """Semantic memory double backed by a shared dict, so separate graph instances
    over the same data dir behave like one persistent store."""

    def __init__(self, store=None, search_results=None, search_error=None, add_error=None):
        self.store = store if store is not None else {}
        self.search_results = search_results or []
        self.search_error = search_error
        self.add_error = add_error
        self.added = []

    def add(self, texts):
        if self.add_error:
            raise self.add_error
        self.added.extend(texts)
        for text in texts:
            self.store[text] = text

    def search(self, query, limit=3):
        if self.search_error:
            raise self.search_error
        if self.search_results:
            return self.search_results[:limit]
        return list(self.store.values())[:limit]


def build(tmp_path, scripted, semantic):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    model = RecordingChatModel(scripted)
    return build_graph(model, settings, memory=semantic), model


def system_note(model) -> str:
    first_invoke = model.seen[0]
    assert isinstance(first_invoke[0], SystemMessage)
    return first_invoke[0].content


def test_pre_agent_injects_fact_and_memory_into_the_agent(tmp_path):
    FactsStore(tmp_path).upsert("reply_language", "中文")
    semantic = FakeSemantic(search_results=["likes concise answers"])
    graph, model = build(tmp_path, [AIMessage(content="好的，用中文简洁回复")], semantic)

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "怎么回复我？"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert result["messages"][-1].content == "好的，用中文简洁回复"
    note = system_note(model)
    assert "reply_language: 中文" in note
    assert "权威" in note
    assert "likes concise answers" in note
    assert "参考" in note
    assert note.index("reply_language") < note.index("likes concise answers")


def test_explicit_message_is_written_back_and_survives_a_new_graph(tmp_path):
    shared = {}
    first_semantic = FakeSemantic(store=shared)
    graph, _model = build(tmp_path, [AIMessage(content="好的，记下了")], first_semantic)
    graph.invoke(
        {"messages": [{"role": "user", "content": "记住 我喜欢简洁回复"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert "记住 我喜欢简洁回复" in first_semantic.added

    second_semantic = FakeSemantic(store=shared)
    second, second_model = build(tmp_path, [AIMessage(content="好的，还是简洁")], second_semantic)
    second.invoke(
        {"messages": [{"role": "user", "content": "在吗"}], "run_id": "r2"},
        {"configurable": {"thread_id": "t2"}},
    )

    assert "我喜欢简洁回复" in system_note(second_model)


def test_ordinary_chat_writes_no_memory(tmp_path):
    semantic = FakeSemantic()
    graph, _ = build(tmp_path, [AIMessage(content="今天晴")], semantic)

    graph.invoke(
        {"messages": [{"role": "user", "content": "今天天气如何"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert semantic.added == []


def test_secret_bearing_message_is_refused_not_memorized(tmp_path):
    semantic = FakeSemantic()
    graph, _ = build(tmp_path, [AIMessage(content="我不会保存它")], semantic)

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "记住 我的 api key 是 sk-abc123def456ghijklmn"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert semantic.added == []
    assert "refused" in result["messages"][-1].content.lower()
    assert "sk-abc123def456ghijklmn" not in result["messages"][-1].content


def test_facts_table_wins_over_semantic_memory_in_the_note(tmp_path):
    FactsStore(tmp_path).upsert("reply_language", "中文")
    semantic = FakeSemantic(search_results=["user prefers English"])
    graph, model = build(tmp_path, [AIMessage(content="好的")], semantic)

    graph.invoke(
        {"messages": [{"role": "user", "content": "用哪种语言回复？"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    note = system_note(model)
    assert "[权威] reply_language: 中文" in note
    assert "[参考] user prefers English" in note
    assert note.index("reply_language") < note.index("user prefers English")


def test_semantic_search_failure_degrades_to_facts_only(tmp_path, caplog):
    FactsStore(tmp_path).upsert("reply_language", "中文")
    semantic = FakeSemantic(search_error=RuntimeError("endpoint unreachable"))
    graph, model = build(tmp_path, [AIMessage(content="好的")], semantic)

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "在吗"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert result["messages"][-1].content == "好的"
    note = system_note(model)
    assert "reply_language: 中文" in note
    assert "[参考]" not in note


def test_semantic_add_failure_does_not_crash_the_turn(tmp_path):
    semantic = FakeSemantic(add_error=RuntimeError("endpoint unreachable"))
    graph, _ = build(tmp_path, [AIMessage(content="好的")], semantic)

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "记住 我喜欢简洁回复"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert result["messages"][-1].content == "好的"
    assert MEMORY_REFUSAL not in result["messages"][-1].content
