"""Local file sandbox: a jailed file-op dispatcher plus the transports that reach it.

`handle` is the single enforcement point. Every path argument is joined under the
sandbox root and resolved; anything that then leaves the root raises ValueError.
Both transports route through it, so local and HTTP calls get the same jail.
Stdlib only, so the sandbox container can run this module without installing Loom.
"""

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

READ_CAP_BYTES = 1024 * 1024


def _jail(root: Path, raw: str, what: str) -> Path:
    """Resolve a client path inside the sandbox root or raise ValueError."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{what} is required and must be a non-empty path string")
    resolved = (root / raw).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"{what} {raw!r} escapes the sandbox")
    return resolved


def _entries(root: Path, raw: str) -> dict:
    target = _jail(root, raw, "path")
    listing = []
    for entry in sorted(target.iterdir(), key=lambda item: item.name):
        listing.append({
            "name": entry.name,
            "type": "dir" if entry.is_dir() else "file",
            "size": entry.stat().st_size if entry.is_file() else 0,
        })
    return {"entries": listing}


def _read(root: Path, args: dict[str, Any]) -> dict:
    target = _jail(root, args.get("path"), "path")
    if target.stat().st_size > READ_CAP_BYTES:
        raise ValueError(f"file exceeds the read cap ({READ_CAP_BYTES} bytes): {target.name}")
    return {"content": target.read_text(encoding="utf-8")}


def _write(root: Path, args: dict[str, Any]) -> dict:
    content = args.get("content")
    if not isinstance(content, str):
        raise ValueError("content is required and must be a string")  # noqa: TRY004 - the sandbox contract raises ValueError for every op error
    target = _jail(root, args.get("path"), "path")
    if target.exists():
        raise ValueError(f"refusing to overwrite an existing path: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": str(target)}


def _mkdir(root: Path, args: dict[str, Any]) -> dict:
    target = _jail(root, args.get("path"), "path")
    target.mkdir(parents=True, exist_ok=True)
    return {"path": str(target)}


def _move(root: Path, args: dict[str, Any]) -> dict:
    source = _jail(root, args.get("src"), "src")
    dest = _jail(root, args.get("dst"), "dst")
    if dest.exists():
        raise ValueError(f"destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    return {"path": str(dest)}


def _delete(root: Path, args: dict[str, Any]) -> dict:
    target = _jail(root, args.get("path"), "path")
    if not target.exists():
        raise FileNotFoundError(f"no such file: {target}")
    if target.is_dir():
        raise ValueError(f"refusing to delete a directory: {target}")
    target.unlink()
    return {"path": str(target)}


_OPS = {
    "list_dir": lambda root, args: _entries(root, args.get("path") or "."),
    "read_file": _read,
    "write_file": _write,
    "mkdir": _mkdir,
    "move_file": _move,
    "delete_file": _delete,
}


def handle(op: str, args: dict, root: Path) -> dict:
    """Run one sandbox op against root. Unknown ops and bad args raise ValueError."""
    handler = _OPS.get(op)
    if handler is None:
        raise ValueError(f"unknown sandbox op: {op!r}")
    if not isinstance(args, dict):
        raise ValueError("args must be an object")  # noqa: TRY004 - the sandbox contract raises ValueError for every op error
    return handler(root, args)


class SandboxTransport(Protocol):
    """How the tool registry reaches the jailed dispatcher: in-process or over HTTP."""

    def call(self, op: str, args: dict) -> dict: ...


class LocalSandboxTransport:
    """Runs `handle` in-process against the configured sandbox root."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def call(self, op: str, args: dict) -> dict:
        return handle(op, args, self._root)


class HttpSandboxTransport:
    """POSTs ops to the sandbox HTTP server (docker compose). Same jail on the other end."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def call(self, op: str, args: dict) -> dict:
        request = urllib.request.Request(
            f"{self._base}/execute",
            data=json.dumps({"op": op, "args": args}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ValueError(self._error_from(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(
                f"sandbox service unreachable at {self._base} ({exc}); "
                "start it with: docker compose up -d --build"
            ) from exc
        if not body.get("ok"):
            raise ValueError(body.get("error", "sandbox call failed"))
        return body["result"]

    def _error_from(self, exc: urllib.error.HTTPError) -> str:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            return str(payload.get("error", exc))
        except (ValueError, OSError):
            return str(exc)
