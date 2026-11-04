"""The working-context block: digest, recent episodic events, and owner feedback.

`build_context_block` is the pure renderer; `build_context_note` is the
resilient loader the graph calls, which turns any store failure into an empty
block so context injection can never break a turn (the same rule the facts
note follows). Everything here is reference: the block tells the model these
lines are never authoritative, only the facts table is.
"""

import logging
import sqlite3
from pathlib import Path

from loom.memory.digest import DigestStore
from loom.memory.episodic import EpisodicStore
from loom.memory.feedback import FeedbackStore

logger = logging.getLogger("loom")

_ENTRY_TEXT_CAP = 200

_HEADER = (
    "Working context: the digest summarizes older activity; episodic events and "
    "owner feedback are reference only. None of these lines are authoritative facts; "
    "on conflict the authoritative facts above win."
)


def _clip(text: str) -> str:
    return text if len(text) <= _ENTRY_TEXT_CAP else text[:_ENTRY_TEXT_CAP]


def build_context_block(digest: str, episodic: "list", feedback: "list", *, entry_cap: int) -> str:
    """Render the injected block; empty string when there is nothing to inject.

    The digest renders first, then the newest episodic entries, then the newest
    feedback. The digest and the lists together count against `entry_cap`, and
    every line is clipped to the same 200 characters audit values use.
    """
    if not digest and not episodic and not feedback:
        return ""
    lines: list[str] = []
    if digest:
        flat = digest.replace("\n", " / ")
        lines.append(f"- [digest] {_clip(flat)}")
    for entry in episodic[: max(entry_cap - len(lines), 0)]:
        lines.append(f"- [episodic] {entry.ts} {entry.kind}: {_clip(entry.text)}")
    for item in feedback[: max(entry_cap - len(lines), 0)]:
        lines.append(f"- [feedback] {item.source}: {_clip(item.text)}")
    return _HEADER + "\n" + "\n".join(lines)


def build_context_note(data_dir: Path, entry_cap: int) -> str:
    """Load the context stores and render the block; failures mean an empty block."""
    try:
        digest = DigestStore(data_dir).get()
        episodes = EpisodicStore(data_dir).recent(entry_cap)
        feedback = FeedbackStore(data_dir).recent(entry_cap)
    except (OSError, sqlite3.Error) as exc:
        logger.warning("context stores unavailable; injecting no context", extra={"error": str(exc)})
        return ""
    return build_context_block(digest, episodes, feedback, entry_cap=entry_cap)
