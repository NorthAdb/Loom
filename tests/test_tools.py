from datetime import datetime

import pytest

from loom.tools import make_tools


def test_registry_declares_side_effect_flags(tmp_path):
    registry = make_tools(tmp_path)

    assert registry["get_time"].side_effect is False
    assert registry["echo"].side_effect is False
    assert registry["propose_sandbox_note"].side_effect is True


def test_get_time_returns_parseable_local_iso(tmp_path):
    value = make_tools(tmp_path)["get_time"].func.invoke({})

    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None


def test_echo_returns_text_unchanged(tmp_path):
    assert make_tools(tmp_path)["echo"].func.invoke({"text": "hello loom"}) == "hello loom"


def test_propose_sandbox_note_writes_under_data_dir(tmp_path):
    registry = make_tools(tmp_path)

    result = registry["propose_sandbox_note"].func.invoke({"filename": "note.txt", "content": "hello"})

    written = tmp_path / "notes" / "note.txt"
    assert result == str(written)
    assert written.read_text(encoding="utf-8") == "hello"


def test_propose_sandbox_note_rejects_escape_outside_data_dir(tmp_path):
    registry = make_tools(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        registry["propose_sandbox_note"].func.invoke({"filename": "../escape.txt", "content": "no"})

    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path.parent / "escape.txt").exists()
