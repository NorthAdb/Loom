import os

import pytest

from loom.sandbox import handle


def make_sandbox(tmp_path):
    return lambda op, args: handle(op, args, tmp_path), tmp_path


def test_write_read_round_trip(tmp_path):
    call, root = make_sandbox(tmp_path)

    written = call("write_file", {"path": "dir/a.txt", "content": "hello"})
    assert written["path"] == str((root / "dir" / "a.txt").resolve())
    assert call("read_file", {"path": "dir/a.txt"})["content"] == "hello"


def test_list_dir_reports_sorted_entries(tmp_path):
    call, _ = make_sandbox(tmp_path)
    call("write_file", {"path": "b.txt", "content": "x"})
    call("write_file", {"path": "a.txt", "content": "yy"})
    call("mkdir", {"path": "sub"})

    entries = call("list_dir", {})["entries"]

    assert [e["name"] for e in entries] == ["a.txt", "b.txt", "sub"]
    assert entries[0] == {"name": "a.txt", "type": "file", "size": 2}
    assert entries[2] == {"name": "sub", "type": "dir", "size": 0}
    assert call("list_dir", {"path": "."}) == call("list_dir", {})


def test_move_delete_round_trip(tmp_path):
    call, root = make_sandbox(tmp_path)
    call("write_file", {"path": "old.txt", "content": "data"})

    moved = call("move_file", {"src": "old.txt", "dst": "new.txt"})
    assert moved["path"] == str((root / "new.txt").resolve())
    assert not (root / "old.txt").exists()
    assert call("read_file", {"path": "new.txt"})["content"] == "data"

    deleted = call("delete_file", {"path": "new.txt"})
    assert deleted["path"] == str((root / "new.txt").resolve())
    assert not (root / "new.txt").exists()


@pytest.mark.parametrize("bad", ["../escape.txt", "..", "a/../../escape.txt"])
def test_relative_escape_is_rejected(tmp_path, bad):
    call, _ = make_sandbox(tmp_path)

    with pytest.raises(ValueError, match="escapes the sandbox"):
        call("write_file", {"path": bad, "content": "no"})
    with pytest.raises(ValueError, match="escapes the sandbox"):
        call("read_file", {"path": bad})
    assert not (tmp_path.parent / "escape.txt").exists()


def test_absolute_path_outside_root_is_rejected(tmp_path):
    call, _ = make_sandbox(tmp_path)
    outside = tmp_path.parent / "outside.txt"

    with pytest.raises(ValueError, match="escapes the sandbox"):
        call("write_file", {"path": str(outside), "content": "no"})

    assert not outside.exists()


def test_symlink_pointing_outside_is_rejected(tmp_path):
    call, root = make_sandbox(tmp_path)
    outside = tmp_path.parent / "symlink-target.txt"
    outside.write_text("host file", encoding="utf-8")
    link = root / "link.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("this host does not grant symlink privileges")

    with pytest.raises(ValueError, match="escapes the sandbox"):
        call("read_file", {"path": "link.txt"})
    with pytest.raises(ValueError, match="escapes the sandbox"):
        call("write_file", {"path": "link.txt", "content": "no"})
    assert outside.read_text(encoding="utf-8") == "host file"


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are a Windows reparse point")
def test_junction_pointing_outside_is_rejected(tmp_path):
    """Junctions need no privilege, so the link escape runs on Windows CI hosts too."""
    import subprocess

    call, root = make_sandbox(tmp_path)
    outside_dir = tmp_path.parent / "junction-target"
    outside_dir.mkdir()
    (outside_dir / "host.txt").write_text("host file", encoding="utf-8")
    link = root / "jlink"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside_dir)], check=False, capture_output=True)
    assert result.returncode == 0, result.stderr

    with pytest.raises(ValueError, match="escapes the sandbox"):
        call("read_file", {"path": "jlink/host.txt"})
    with pytest.raises(ValueError, match="escapes the sandbox"):
        call("write_file", {"path": "jlink/host.txt", "content": "no"})
    assert (outside_dir / "host.txt").read_text(encoding="utf-8") == "host file"


def test_empty_path_is_rejected(tmp_path):
    call, _ = make_sandbox(tmp_path)

    with pytest.raises(ValueError):
        call("read_file", {"path": ""})
    with pytest.raises(ValueError):
        call("write_file", {"path": "  ", "content": "x"})


def test_write_refuses_to_overwrite(tmp_path):
    call, root = make_sandbox(tmp_path)
    call("write_file", {"path": "a.txt", "content": "first"})

    with pytest.raises(ValueError, match="overwrite"):
        call("write_file", {"path": "a.txt", "content": "second"})

    assert (root / "a.txt").read_text(encoding="utf-8") == "first"


def test_move_refuses_existing_destination(tmp_path):
    call, root = make_sandbox(tmp_path)
    call("write_file", {"path": "a.txt", "content": "a"})
    call("write_file", {"path": "b.txt", "content": "b"})

    with pytest.raises(ValueError, match="destination already exists"):
        call("move_file", {"src": "a.txt", "dst": "b.txt"})

    assert (root / "a.txt").exists() and (root / "b.txt").read_text(encoding="utf-8") == "b"


def test_delete_refuses_directories(tmp_path):
    call, root = make_sandbox(tmp_path)
    call("mkdir", {"path": "sub"})

    with pytest.raises(ValueError, match="directory"):
        call("delete_file", {"path": "sub"})

    assert (root / "sub").is_dir()


def test_read_cap_rejects_large_file(tmp_path):
    call, root = make_sandbox(tmp_path)
    big = root / "big.bin"
    big.write_bytes(b"x" * (1024 * 1024 + 1))

    with pytest.raises(ValueError, match="file exceeds the read cap"):
        call("read_file", {"path": "big.bin"})


def test_read_file_at_exactly_one_mib_is_allowed(tmp_path):
    call, root = make_sandbox(tmp_path)
    (root / "edge.bin").write_bytes(b"x" * (1024 * 1024))

    assert len(call("read_file", {"path": "edge.bin"})["content"]) == 1024 * 1024


def test_unknown_op_raises(tmp_path):
    call, _ = make_sandbox(tmp_path)

    with pytest.raises(ValueError, match="unknown sandbox op"):
        call("run_shell", {})


def test_missing_argument_raises_not_crashes(tmp_path):
    call, _ = make_sandbox(tmp_path)

    with pytest.raises(ValueError):
        call("read_file", {})
    with pytest.raises(ValueError):
        call("write_file", {"path": "x.txt"})


def test_transports_share_the_dispatcher(tmp_path):
    from loom.sandbox import LocalSandboxTransport

    (tmp_path / "a.txt").write_text("via local", encoding="utf-8")

    assert LocalSandboxTransport(tmp_path).call("read_file", {"path": "a.txt"})["content"] == "via local"
