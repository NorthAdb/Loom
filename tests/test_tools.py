from datetime import datetime

import pytest

from loom.config import Settings
from loom.tools import make_tools


def make_registry(tmp_path, sandbox_root=None):
    settings = Settings(
        loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=sandbox_root or tmp_path / "sandbox"
    )
    return make_tools(settings), sandbox_root or tmp_path / "sandbox"


def test_registry_declares_exactly_the_expected_side_effect_flags(tmp_path):
    registry, _ = make_registry(tmp_path)

    assert set(registry) == {
        "get_time", "echo", "list_dir", "read_file", "write_file", "mkdir", "move_file", "delete_file",
        "propose_sandbox_note",
    }
    assert {name for name, spec in registry.items() if spec.side_effect} == {
        "write_file", "mkdir", "move_file", "delete_file", "propose_sandbox_note",
    }
    assert {name for name, spec in registry.items() if not spec.side_effect} == {
        "get_time", "echo", "list_dir", "read_file",
    }


def test_get_time_returns_parseable_local_iso(tmp_path):
    value = make_registry(tmp_path)[0]["get_time"].func.invoke({})

    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None


def test_echo_returns_text_unchanged(tmp_path):
    registry, _ = make_registry(tmp_path)

    assert registry["echo"].func.invoke({"text": "hello loom"}) == "hello loom"


def test_propose_sandbox_note_writes_under_data_dir(tmp_path):
    registry, _ = make_registry(tmp_path)

    result = registry["propose_sandbox_note"].func.invoke({"filename": "note.txt", "content": "hello"})

    written = tmp_path / "notes" / "note.txt"
    assert result == str(written)
    assert written.read_text(encoding="utf-8") == "hello"


def test_propose_sandbox_note_rejects_escape_outside_data_dir(tmp_path):
    registry, _ = make_registry(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        registry["propose_sandbox_note"].func.invoke({"filename": "../escape.txt", "content": "no"})

    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path.parent / "escape.txt").exists()


def test_file_tools_render_transport_results(tmp_path):
    registry, sandbox_root = make_registry(tmp_path)

    write = registry["write_file"].func.invoke({"path": "docs/a.txt", "content": "body"})

    assert "wrote" in write
    assert (sandbox_root / "docs" / "a.txt").read_text(encoding="utf-8") == "body"

    assert registry["read_file"].func.invoke({"path": "docs/a.txt"}) == "body"

    listing = registry["list_dir"].func.invoke({"path": "."})
    assert "docs/" in listing

    assert "ready" in registry["mkdir"].func.invoke({"path": "docs"})

    moved = registry["move_file"].func.invoke({"src": "docs/a.txt", "dst": "docs/b.txt"})
    assert "moved" in moved and "b.txt" in moved
    assert (sandbox_root / "docs" / "b.txt").read_text(encoding="utf-8") == "body"

    deleted = registry["delete_file"].func.invoke({"path": "docs/b.txt"})
    assert "deleted" in deleted
    assert not (sandbox_root / "docs" / "b.txt").exists()


def test_file_tools_surface_jail_errors_as_error_text(tmp_path):
    registry, _ = make_registry(tmp_path)

    with pytest.raises(ValueError, match="escapes the sandbox"):
        registry["read_file"].func.invoke({"path": "../outside.txt"})

    with pytest.raises(ValueError, match="escapes the sandbox"):
        registry["write_file"].func.invoke({"path": "../outside.txt", "content": "x"})
    assert not (tmp_path.parent / "outside.txt").exists()
