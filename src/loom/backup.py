"""Zip archives of the Loom data dir behind `loom backup` and `loom restore`.

An archive pins exactly the five roots in BACKUP_ROOTS; everything else in the
data dir (spend ledgers, the semantic memory store, .env) is never included,
so an archive cannot become a secrets exfiltration path.
"""

import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

# The five roots an archive may contain. Adding a data-dir artifact means
# adding it here and pinning it in tests/test_backup.py.
BACKUP_ROOTS = ("facts.db", "routines.json", "approvals", "audit", "checkpoints")


def _archive_files(data_dir: Path):
    for root in BACKUP_ROOTS:
        path = data_dir / root
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(file for file in path.rglob("*") if file.is_file())


def _next_archive_path(destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    stem = f"loom-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    candidate = destination_dir / f"{stem}.zip"
    counter = 1
    while candidate.exists():
        candidate = destination_dir / f"{stem}-{counter}.zip"
        counter += 1
    return candidate


def create_backup(data_dir: Path, destination_dir: Path) -> Path:
    """Zip the five data-dir roots into `<destination_dir>/loom-backup-<stamp>.zip`.

    The stamp is local time; an existing name gets a `-1`, `-2`, ... suffix
    instead of being overwritten. Missing roots are simply absent from the
    archive. Entries keep their data-dir-relative layout. facts.db is copied
    as the file stands, so run the backup while Loom is idle: a sqlite file
    captured mid-write can be torn.
    """
    data_dir = Path(data_dir)
    archive_path = _next_archive_path(Path(destination_dir))
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in _archive_files(data_dir):
            archive.write(file, file.relative_to(data_dir).as_posix())
    return archive_path


def _member_target(data_dir: Path, member: str) -> Path:
    relative = PurePosixPath(member)
    if not member or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"refusing unsafe archive member: {member!r}")
    # Zip-slip guard: the resolved target must stay strictly inside the data
    # dir, so a crafted archive (absolute paths, .. segments, Windows drives)
    # cannot write outside it.
    root = data_dir.resolve()
    resolved = (data_dir / Path(*relative.parts)).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f"refusing archive member outside the data dir: {member!r}")
    return resolved


def restore_backup(archive_path: Path, data_dir: Path, overwrite: bool) -> list[str]:
    """Write the archive's members back into the data dir; return the paths restored, sorted.

    Refuses with a ValueError naming the existing paths when any member would
    land on an existing file and `overwrite` is False; with `overwrite` True it
    replaces exactly the archive's members. Every member is validated to land
    inside the data dir before anything is written.
    """
    data_dir = Path(data_dir)
    with zipfile.ZipFile(archive_path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        targets = {info.filename: _member_target(data_dir, info.filename) for info in members}
        if not overwrite:
            existing = [str(targets[info.filename]) for info in members if targets[info.filename].exists()]
            if existing:
                raise ValueError("refusing to overwrite existing file(s): " + ", ".join(existing))
        restored = []
        for info in members:
            target = targets[info.filename]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            restored.append(PurePosixPath(info.filename).as_posix())
    return sorted(restored)
