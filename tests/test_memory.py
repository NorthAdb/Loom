import pytest

from loom.memory import (
    Fact,
    FactsStore,
    build_memory_note,
    contains_secret,
    has_explicit_memory_intent,
)


def make_fact(key: str, value: str) -> Fact:
    return Fact(id=f"id-{key}", key=key, value=value, source="human", updated_at="2026-10-31T10:00:00+08:00", confidence=1.0)


def test_facts_round_trip(tmp_path):
    store = FactsStore(tmp_path)

    stored = store.upsert("reply_language", "中文")

    assert stored.key == "reply_language"
    assert stored.value == "中文"
    assert stored.source == "human"
    assert stored.confidence == 1.0
    assert store.get("reply_language") == stored
    assert [fact.key for fact in store.list()] == ["reply_language"]


def test_upsert_correction_overwrites_value_and_updated_at(tmp_path):
    store = FactsStore(tmp_path)
    first = store.upsert("reply_language", "中文")

    second = store.upsert("reply_language", "English")

    assert second.value == "English"
    assert second.updated_at >= first.updated_at
    assert store.get("reply_language").value == "English"
    assert len(store.list()) == 1


def test_upsert_persists_across_reopened_stores(tmp_path):
    FactsStore(tmp_path).upsert("reply_language", "中文")

    reopened = FactsStore(tmp_path)

    assert reopened.get("reply_language").value == "中文"


def test_delete_removes_row_and_reports_missing(tmp_path):
    store = FactsStore(tmp_path)
    store.upsert("a", "1")

    removed = store.delete("a")

    assert removed.key == "a"
    assert store.get("a") is None
    assert store.list() == []
    with pytest.raises(KeyError):
        store.delete("a")


def test_sensitive_key_rejected(tmp_path):
    store = FactsStore(tmp_path)

    with pytest.raises(ValueError):
        store.upsert("api_key", "not-a-real-secret-value")

    assert store.list() == []


def test_sk_prefixed_value_rejected(tmp_path):
    store = FactsStore(tmp_path)

    with pytest.raises(ValueError):
        store.upsert("note", "my key is sk-abc123def456ghijkl")

    assert store.list() == []


def test_note_lists_facts_before_memories_with_labels():
    note = build_memory_note(
        [make_fact("reply_language", "中文")],
        ["likes concise answers"],
    )

    assert "[权威] reply_language: 中文" in note
    assert "[参考] likes concise answers" in note
    assert note.index("reply_language") < note.index("likes concise answers")
    assert "conflict" in note  # the note states the table-wins rule


def test_note_limit_enforced_memories_cut_first():
    facts = [make_fact(f"key_{i}", f"value {i}") for i in range(3)]

    note = build_memory_note(facts, ["memory one", "memory two", "memory three"], limit=4)
    entries = [line for line in note.splitlines() if line.startswith("- ")]

    assert len(entries) == 4
    assert "memory one" in note
    assert "memory two" not in note
    assert "memory three" not in note


def test_note_empty_in_empty_out():
    assert build_memory_note([], []) == ""


@pytest.mark.parametrize(
    "text",
    [
        "my key is sk-abc123def456ghijkl",
        "ghp_ABCDEFGHijklmnOPQRstuvwx",
        "AKIAIOSFODNN7EXAMPLETOKEN",
        "xox-shaped-token-not-real",
        "password: " + "x" * 24,  # exactly at the long-token threshold
        "the access token is ABCDEFGHIJKLMNOPabcdefgh",
    ],
)
def test_secret_shaped_text_is_detected(text):
    assert contains_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "password: " + "x" * 23,  # one under the token threshold
        "记住 我喜欢简洁回复",
        "以后文件按扩展名整理",
        "reply language is chinese",
        "always use dark mode",
    ],
)
def test_ordinary_text_is_not_flagged(text):
    assert not contains_secret(text)


def test_explicit_memory_intent_patterns():
    for text in (
        "记住 我喜欢简洁回复",
        "From now on reply in Chinese",
        "总是用中文回复",
        "以后文件按扩展名整理",
        "不要再发长回复",
        "I prefer concise answers",
        "always reply in Chinese",
    ):
        assert has_explicit_memory_intent(text), text

    for text in ("今天天气如何", "hello there", "what time is it?"):
        assert not has_explicit_memory_intent(text), text


@pytest.mark.parametrize(
    "value",
    [
        "a7Kx9mQ2pL5vB8nR",  # 16 distinct characters: 4 bits/char, like a random token
        "Zm9vYmFyQmF6cXV4",  # base64-shaped run the provider prefixes never catch
        "password hunter2",  # label + value shape, refused at any length
        "passwd: hunter2",
        "pwd=hunter2",
    ],
)
def test_facts_value_guard_refuses_secret_shapes(tmp_path, value):
    with pytest.raises(ValueError, match="looks like it carries a secret"):
        FactsStore(tmp_path).upsert("note", value)


@pytest.mark.parametrize(
    "value",
    [
        "中文",
        "prefers short replies with headers",
        "reply_language 中文",
        "always summarize the week on Monday mornings",
    ],
)
def test_facts_value_guard_passes_benign_values(tmp_path, value):
    stored = FactsStore(tmp_path).upsert("note", value)

    assert stored.value == value
