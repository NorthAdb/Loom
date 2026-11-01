"""loom backup and loom restore: five-roots archives, guarded restore, CLI entry points."""

import gc
import json
import shutil
import zipfile
from datetime import date, datetime

import pytest

import loom.backup
import loom.cli
from loom.backup import BACKUP_ROOTS, create_backup, restore_backup
from loom.memory import FactsStore
from loom.pending import PENDING, PendingApproval, PendingStore
from loom.routines import Routine, RoutineStore


def _wipe(data_dir):
    # FactsStore opens a fresh sqlite connection per call and relies on the
    # garbage collector to close it; force that before Windows lets go of facts.db.
    gc.collect()
    for root in BACKUP_ROOTS:
        path = data_dir / root
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def _seed(data_dir):
    """Fill the five roots through their real stores; plain bytes for the checkpoint file."""
    FactsStore(data_dir).upsert("reply_language", "中文")
    FactsStore(data_dir).upsert("operator_name", "Loon")
    RoutineStore(data_dir).add(Routine(id="daily_tidy", name="Daily tidy", cron="0 9 * * *", prompt="Tidy the sandbox."))
    PendingStore(data_dir).add(
        PendingApproval(
            pending_id="p1",
            thread_id="routine:daily_tidy:1",
            routine_id="daily_tidy",
            tools=[{"name": "write_file", "args": {"path": "note.txt"}}],
            reason="side effect",
            created_at="2026-11-01T09:00:00+08:00",
            status=PENDING,
        )
    )
    audit = data_dir / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    with (audit / f"{date.today().strftime('%Y%m%d')}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "t1", "decision": "approved"}) + "\n")
        f.write(json.dumps({"ts": "t2", "decision": "rejected"}) + "\n")
    checkpoints = data_dir / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    (checkpoints / "threads.sqlite").write_bytes(b"checkpoint-bytes")


def test_backup_restore_round_trip_through_real_stores(tmp_path):
    data_dir = tmp_path / "data"
    _seed(data_dir)
    audit_path = next((data_dir / "audit").glob("*.jsonl"))
    audit_before = audit_path.read_bytes()

    archive = create_backup(data_dir, tmp_path / "dest")
    assert archive.name.startswith("loom-backup-") and archive.name.endswith(".zip")
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == [
            "facts.db",
            "routines.json",
            "approvals/pending.json",
            str(audit_path.relative_to(data_dir)).replace("\\", "/"),
            "checkpoints/threads.sqlite",
        ]

    _wipe(data_dir)
    assert list(data_dir.rglob("*")) == []

    restored = restore_backup(archive, data_dir, overwrite=False)
    assert restored == [
        "approvals/pending.json",
        str(audit_path.relative_to(data_dir)).replace("\\", "/"),
        "checkpoints/threads.sqlite",
        "facts.db",
        "routines.json",
    ]

    fact = FactsStore(data_dir).get("reply_language")
    assert fact is not None and fact.value == "中文"
    assert [routine.id for routine in RoutineStore(data_dir).load()] == ["daily_tidy"]
    waiting = PendingStore(data_dir).list_pending()
    assert [record.pending_id for record in waiting] == ["p1"]
    assert [record.tools[0]["name"] for record in waiting] == ["write_file"]
    assert audit_path.read_bytes() == audit_before
    assert (data_dir / "checkpoints" / "threads.sqlite").read_bytes() == b"checkpoint-bytes"


def test_backup_of_empty_data_dir_and_restore_is_a_noop(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    archive = create_backup(data_dir, tmp_path / "dest")
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == []
    assert restore_backup(archive, data_dir, overwrite=False) == []


def test_restore_refuses_existing_targets_then_overwrites_with_force(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "audit").mkdir(parents=True)
    (data_dir / "audit" / "20261101.jsonl").write_text("old\n", encoding="utf-8")
    source = tmp_path / "source"
    (source / "audit").mkdir(parents=True)
    (source / "audit" / "20261101.jsonl").write_text("new\n", encoding="utf-8")
    archive = create_backup(source, tmp_path / "dest")

    with pytest.raises(ValueError, match="20261101.jsonl"):
        restore_backup(archive, data_dir, overwrite=False)
    assert (data_dir / "audit" / "20261101.jsonl").read_text(encoding="utf-8") == "old\n"

    assert restore_backup(archive, data_dir, overwrite=True) == ["audit/20261101.jsonl"]
    assert (data_dir / "audit" / "20261101.jsonl").read_text(encoding="utf-8") == "new\n"


def test_restore_refuses_members_that_escape_the_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    archive = tmp_path / "crafted.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "nope")

    with pytest.raises(ValueError, match=r"\.\./evil\.txt"):
        restore_backup(archive, data_dir, overwrite=True)

    assert not (tmp_path / "evil.txt").exists()
    assert list(data_dir.rglob("*")) == []


class _Fixed(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 1, 12, 0, 0)


def test_backup_names_collide_into_numbered_suffixes(tmp_path, monkeypatch):
    monkeypatch.setattr(loom.backup, "datetime", _Fixed)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dest = tmp_path / "dest"

    names = [create_backup(data_dir, dest).name for _ in range(3)]

    assert names == [
        "loom-backup-20261101-120000.zip",
        "loom-backup-20261101-120000-1.zip",
        "loom-backup-20261101-120000-2.zip",
    ]


def test_archive_never_leaves_the_five_roots(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "audit").mkdir(parents=True)
    (data_dir / "audit" / "20261101.jsonl").write_text("{}\n", encoding="utf-8")
    (data_dir / "routines.json").write_text("[]\n", encoding="utf-8")
    (data_dir / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (data_dir / "spend").mkdir()
    (data_dir / "spend" / "2026-11-01.jsonl").write_text("{}\n", encoding="utf-8")
    (data_dir / "memory").mkdir()
    (data_dir / "memory" / "vectors.db").write_bytes(b"x")

    archive = create_backup(data_dir, tmp_path / "dest")

    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == ["routines.json", "audit/20261101.jsonl"]


def test_cli_backup_and_restore_round_trip(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path / "data"))
    data_dir = tmp_path / "data"
    _seed(data_dir)
    dest = tmp_path / "backups"

    assert loom.cli.main(["backup", str(dest)]) == 0
    out = capsys.readouterr().out
    archive = next(dest.glob("loom-backup-*.zip"))
    assert archive.name in out
    assert "(5 files)" in out

    _wipe(data_dir)
    assert loom.cli.main(["restore", str(archive)]) == 0
    out = capsys.readouterr().out
    assert "facts.db" in out and "approvals/pending.json" in out
    assert FactsStore(data_dir).get("reply_language").value == "中文"
    assert [record.pending_id for record in PendingStore(data_dir).list_pending()] == ["p1"]


def test_cli_restore_conflict_exits_1_without_force(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path / "data"))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "routines.json").write_text("old\n", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    (source / "routines.json").write_text("new\n", encoding="utf-8")
    archive = create_backup(source, tmp_path / "dest")

    assert loom.cli.main(["restore", str(archive)]) == 1
    assert "error: refusing to overwrite existing file(s)" in capsys.readouterr().out
    assert (data_dir / "routines.json").read_text(encoding="utf-8") == "old\n"

    assert loom.cli.main(["restore", str(archive), "--force"]) == 0
    assert (data_dir / "routines.json").read_text(encoding="utf-8") == "new\n"
