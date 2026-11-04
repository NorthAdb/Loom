"""Behavior contracts for the Gate 6 context stores: episodic, digest, feedback, compact."""

import sqlite3
import sys

import pytest

from loom.memory import (
    DigestStore,
    EpisodicStore,
    FactsStore,
    FeedbackStore,
    build_context_block,
    build_context_note,
    build_digest,
    compact,
)
from loom.memory.compact import _row_to_entry


def test_episodic_append_returns_the_entry_and_recent_is_newest_first(tmp_path):
    store = EpisodicStore(tmp_path)
    first = store.append("turn", "hello there")
    second = store.append("inbound", "a simulated email arrived")

    assert first.seq < second.seq
    assert first.folded is False
    recent = store.recent()
    assert [entry.seq for entry in recent] == [second.seq, first.seq]
    assert store.count_unfolded() == 2


def test_episodic_text_is_capped_and_secret_shaped_text_is_refused(tmp_path):
    store = EpisodicStore(tmp_path)
    entry = store.append("turn", "x" * 5000)

    assert len(entry.text) == 2000
    with pytest.raises(ValueError, match="carries a secret"):
        store.append("turn", "the key is sk-0123456789abcdefghijklmnop")
    assert store.count_unfolded() == 1


def test_digest_is_empty_until_compact_runs_and_has_no_setter_path(tmp_path):
    assert DigestStore(tmp_path).get() == ""
    assert not hasattr(DigestStore(tmp_path), "set")


def test_compact_folds_entries_into_the_digest_and_marks_them(tmp_path):
    store = EpisodicStore(tmp_path)
    store.append("turn", "first event")
    store.append("turn", "second event")

    result = compact(tmp_path)

    assert result.folded == 2
    assert result.old_digest == ""
    assert "first event" in result.new_digest
    assert "second event" in result.new_digest
    assert DigestStore(tmp_path).get() == result.new_digest
    assert store.count_unfolded() == 0
    assert store.recent(unfolded_only=False)[0].folded is True


def test_compact_with_nothing_unfolded_writes_nothing(tmp_path):
    store = EpisodicStore(tmp_path)
    store.append("turn", "only event")
    compact(tmp_path)
    before = DigestStore(tmp_path).get()

    result = compact(tmp_path)

    assert result.folded == 0
    assert result.old_digest == before == result.new_digest
    assert store.count_unfolded() == 0


def test_compact_continues_folding_across_runs(tmp_path):
    store = EpisodicStore(tmp_path)
    store.append("turn", "event one")
    compact(tmp_path)
    store.append("turn", "event two")

    result = compact(tmp_path)

    assert result.folded == 1
    assert "event one" in result.old_digest
    assert "event two" in result.new_digest


def test_build_digest_keeps_the_most_recent_lines_under_the_cap():
    entries = [_row_to_entry((seq, f"2026-11-05T0{seq}:00:00+08:00", "turn", f"event {seq}", False)) for seq in range(1, 6)]

    digest = build_digest("old digest line", entries, char_cap=120)

    assert digest.startswith("- 2026-11-05T04")
    assert "old digest line" not in digest
    assert "event 5" in digest
    assert len(digest) <= 120


def test_failed_compact_keeps_the_old_digest_and_the_entries(tmp_path, monkeypatch):
    store = EpisodicStore(tmp_path)
    store.append("turn", "first event")
    compact(tmp_path)
    store.append("turn", "second event")
    before = DigestStore(tmp_path).get()

    def boom(old, entries, *, char_cap=4000):
        raise RuntimeError("disk full mid-compact")

    # loom.memory.compact resolves to the re-exported compact function on the
    # package, so the patch targets the module object through sys.modules.
    monkeypatch.setattr(sys.modules["loom.memory.compact"], "build_digest", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        compact(tmp_path)

    assert DigestStore(tmp_path).get() == before
    assert store.count_unfolded() == 1
    unfolded = store.oldest_unfolded()
    assert [entry.text for entry in unfolded] == ["second event"]


def test_feedback_add_and_recent_with_secret_guard(tmp_path):
    store = FeedbackStore(tmp_path)
    store.add("owner", "keep replies short")
    store.add("owner", "never book flights before 9am")

    recent = store.recent()
    assert [entry.text for entry in recent] == ["never book flights before 9am", "keep replies short"]
    with pytest.raises(ValueError, match="carries a secret"):
        store.add("owner", "the api token is aaaabbbbccccddddeeeeffff1111")


def test_context_block_composes_digest_episodic_and_feedback_with_a_cap(tmp_path):
    EpisodicStore(tmp_path).append("turn", "an event")
    FeedbackStore(tmp_path).add("owner", "a correction")
    episodes = EpisodicStore(tmp_path).recent(5)
    feedback = FeedbackStore(tmp_path).recent(5)

    block = build_context_block("a digest line", episodes, feedback, entry_cap=6)

    assert block.startswith("Working context:")
    assert "- [digest] a digest line" in block
    assert "- [episodic] " in block and "an event" in block
    assert "- [feedback] owner: a correction" in block

    empty = build_context_block("", [], [], entry_cap=6)
    assert empty == ""

    capped = build_context_block("d", episodes, feedback, entry_cap=1)
    # The digest fills the one slot; episodic and feedback get nothing.
    assert capped.count("\n- [") == 1
    assert "- [digest] d" in capped
    assert "[episodic]" not in capped
    assert "[feedback]" not in capped


def test_context_stores_never_write_facts(tmp_path):
    """The handoff's rule: inbound and working context do not silently write facts."""
    store = EpisodicStore(tmp_path)
    store.append("inbound", "please remember my meeting is at 3")
    store.append("turn", "the model answered")
    FeedbackStore(tmp_path).add("owner", "remember to double-check")
    compact(tmp_path)

    assert FactsStore(tmp_path).list() == []


def test_build_context_note_survives_a_broken_store(tmp_path, monkeypatch):
    EpisodicStore(tmp_path).append("turn", "an event")

    def broken_get(self):
        raise sqlite3.OperationalError("context.db is corrupt")

    monkeypatch.setattr(DigestStore, "get", broken_get)
    assert build_context_note(tmp_path, 6) == ""


def test_build_context_note_renders_the_stores(tmp_path):
    EpisodicStore(tmp_path).append("turn", "an event")
    FeedbackStore(tmp_path).add("owner", "a correction")
    compact(tmp_path)

    note = build_context_note(tmp_path, 6)

    assert "[digest]" in note
    assert "an event" in note
    assert "[feedback] owner: a correction" in note
