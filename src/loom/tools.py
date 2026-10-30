"""Tool registry with declared side-effect flags; the approval gate reads the flag."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from langchain_core.tools import BaseTool, tool

_data_dir: Path | None = None


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool and whether it changes state outside the process."""

    name: str
    description: str
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


@tool
def propose_sandbox_note(filename: str, content: str) -> str:
    """Write content to a note file under the configured data directory's notes folder.

    Filenames that resolve outside the notes directory are rejected with ValueError;
    nothing is written in that case. Returns the written path.
    """
    if _data_dir is None:
        raise RuntimeError("tools module is not configured with a data dir")
    notes_dir = (_data_dir / "notes").resolve()
    target = (notes_dir / filename).resolve()
    if not filename or not target.is_relative_to(notes_dir):
        raise ValueError(f"filename escapes the notes directory: {filename!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)


def configure(data_dir: Path) -> None:
    """Point the sandbox tools at the data directory; called once by the graph builder."""
    global _data_dir
    _data_dir = Path(data_dir)


REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec("get_time", get_time.description, get_time, side_effect=False),
        ToolSpec("echo", echo.description, echo, side_effect=False),
        ToolSpec(
            "propose_sandbox_note",
            propose_sandbox_note.description,
            propose_sandbox_note,
            side_effect=True,
        ),
    )
}


def registry_funcs() -> list[BaseTool]:
    """Return the registered tools as callables for model.bind_tools."""
    return [spec.func for spec in REGISTRY.values()]
