"""Tool registry with declared side-effect flags; the approval gate reads the flag."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from loom.config import Settings
from loom.sandbox import HttpSandboxTransport, LocalSandboxTransport, SandboxTransport


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool and whether it changes state outside the process."""

    func: BaseTool
    side_effect: bool


@tool
def get_time() -> str:
    """Return the current local time as an ISO-8601 string."""
    return datetime.now().astimezone().isoformat()


@tool
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


def _render_listing(result: dict) -> str:
    entries = result["entries"]
    if not entries:
        return "(empty directory)"
    return "\n".join(
        f"{entry['name']}/" if entry["type"] == "dir" else f"{entry['name']} ({entry['size']} bytes)"
        for entry in entries
    )


def make_tools(settings: Settings) -> dict[str, ToolSpec]:
    """Build the registry; file tools reach the sandbox through one shared transport."""

    if settings.loom_sandbox_url:
        transport: SandboxTransport = HttpSandboxTransport(settings.loom_sandbox_url)
    else:
        transport = LocalSandboxTransport(settings.loom_sandbox_root)

    @tool
    def list_dir(path: str = ".") -> str:
        """List one directory inside the sandbox: names, types, and file sizes.

        `path` is relative to the sandbox root. Paths that leave the sandbox raise an error.
        """
        return _render_listing(transport.call("list_dir", {"path": path}))

    @tool
    def read_file(path: str) -> str:
        """Read a text file inside the sandbox and return its content.

        Files above 1 MiB are rejected. Paths that leave the sandbox raise an error.
        """
        return transport.call("read_file", {"path": path})["content"]

    @tool
    def write_file(path: str, content: str) -> str:
        """Write a text file inside the sandbox, creating parent directories as needed.

        Refuses to overwrite an existing file. Side effect: pauses for approval.
        """
        return f"wrote {transport.call('write_file', {'path': path, 'content': content})['path']}"

    @tool
    def mkdir(path: str) -> str:
        """Create a directory (and parents) inside the sandbox; existing ones are fine.

        Side effect: pauses for approval.
        """
        return f"directory ready: {transport.call('mkdir', {'path': path})['path']}"

    @tool
    def move_file(src: str, dst: str) -> str:
        """Move or rename a file inside the sandbox to a destination that must not exist.

        Side effect: pauses for approval.
        """
        return f"moved {src} to {transport.call('move_file', {'src': src, 'dst': dst})['path']}"

    @tool
    def delete_file(path: str) -> str:
        """Delete one regular file inside the sandbox; directories are refused.

        Side effect: pauses for approval.
        """
        return f"deleted {transport.call('delete_file', {'path': path})['path']}"

    @tool
    def propose_sandbox_note(filename: str, content: str) -> str:
        """Write content to a note file under the data directory's notes folder.

        Filenames that resolve outside the notes directory are rejected with ValueError;
        nothing is written in that case. Returns the written path.
        """
        notes_dir = (Path(settings.loom_data_dir) / "notes").resolve()
        target = (notes_dir / filename).resolve()
        if not filename or not target.is_relative_to(notes_dir):
            raise ValueError(f"filename escapes the notes directory: {filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target)

    return {
        "get_time": ToolSpec(get_time, side_effect=False),
        "echo": ToolSpec(echo, side_effect=False),
        "list_dir": ToolSpec(list_dir, side_effect=False),
        "read_file": ToolSpec(read_file, side_effect=False),
        "write_file": ToolSpec(write_file, side_effect=True),
        "mkdir": ToolSpec(mkdir, side_effect=True),
        "move_file": ToolSpec(move_file, side_effect=True),
        "delete_file": ToolSpec(delete_file, side_effect=True),
        "propose_sandbox_note": ToolSpec(propose_sandbox_note, side_effect=True),
    }
