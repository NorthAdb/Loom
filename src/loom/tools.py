"""Tool registry with declared side-effect flags; the approval gate reads the flag."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from langchain_core.tools import BaseTool, tool


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


def make_tools(data_dir: Path) -> dict[str, ToolSpec]:
    """Build the registry; sandbox tools close over the given data directory."""

    @tool
    def propose_sandbox_note(filename: str, content: str) -> str:
        """Write content to a note file under the data directory's notes folder.

        Filenames that resolve outside the notes directory are rejected with ValueError;
        nothing is written in that case. Returns the written path.
        """
        notes_dir = (Path(data_dir) / "notes").resolve()
        target = (notes_dir / filename).resolve()
        if not filename or not target.is_relative_to(notes_dir):
            raise ValueError(f"filename escapes the notes directory: {filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target)

    return {
        "get_time": ToolSpec(get_time, side_effect=False),
        "echo": ToolSpec(echo, side_effect=False),
        "propose_sandbox_note": ToolSpec(propose_sandbox_note, side_effect=True),
    }
