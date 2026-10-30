from datetime import datetime

import pytest

from loom import tools


@pytest.fixture(autouse=True)
def configured_data_dir(tmp_path):
    """Point the sandbox tools at a per-test data directory."""
    tools.configure(tmp_path)
    return tmp_path


def test_registry_declares_side_effect_flags():
    assert tools.REGISTRY["get_time"].side_effect is False
    assert tools.REGISTRY["echo"].side_effect is False
    assert tools.REGISTRY["propose_sandbox_note"].side_effect is True


def test_get_time_returns_parseable_local_iso():
    value = tools.REGISTRY["get_time"].func.invoke({})
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None


def test_echo_returns_text_unchanged():
    assert tools.REGISTRY["echo"].func.invoke({"text": "hello loom"}) == "hello loom"


def test_propose_sandbox_note_writes_under_configured_data_dir(tmp_path):
    result = tools.REGISTRY["propose_sandbox_note"].func.invoke(
        {"filename": "note.txt", "content": "hello"}
    )

    written = tmp_path / "notes" / "note.txt"
    assert result == str(written)
    assert written.read_text(encoding="utf-8") == "hello"


def test_propose_sandbox_note_rejects_escape_outside_data_dir(tmp_path):
    # Documented behavior: a filename that resolves outside the notes
    # directory raises ValueError and writes nothing.
    with pytest.raises(ValueError, match="escapes"):
        tools.REGISTRY["propose_sandbox_note"].func.invoke(
            {"filename": "../escape.txt", "content": "no"}
        )
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path.parent / "escape.txt").exists()
