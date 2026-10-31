"""Tool registry with declared side-effect flags; the approval gate reads the flag."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, tool

from loom.calendar import HttpCalendarTransport, parse_events, summarize
from loom.config import Settings
from loom.mail import MAIL_NOT_CONFIGURED, ImapMailTransport, MailTransport, build_draft_message
from loom.sandbox import HttpSandboxTransport, LocalSandboxTransport, SandboxTransport

_CALENDAR_NOT_CONFIGURED = "calendar is not configured; set LOOM_ICAL_URL in .env"
_RANGES = {"today": (0, 1, "today"), "tomorrow": (1, 2, "tomorrow"), "week": (0, 7, "the next 7 days")}


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


def _iso_boundary(text: str, tz: ZoneInfo, which: str) -> datetime:
    """An ISO date or datetime argument as an aware datetime in tz."""
    try:
        value = datetime.fromisoformat(text.strip())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{which} must be an ISO date or datetime, got {text!r}") from exc
    return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)


def build_mail_transport(settings: Settings) -> MailTransport | None:
    """The IMAP transport for the registry, or None when LOOM_IMAP_HOST is unset."""
    return ImapMailTransport(settings) if settings.imap_host else None


def make_tools(settings: Settings, transports: dict | None = None) -> dict[str, ToolSpec]:
    """Build the registry; file tools reach the sandbox through one shared transport.

    `transports` (a mapping with optional "calendar" and "mail" entries) is the
    test seam, like build_graph's `memory`: a missing entry falls back to the
    transport built from settings. Unconfigured mail/calendar tools stay in the
    registry but raise at call time, so the model always sees the full surface.
    """
    chosen = transports or {}
    if settings.loom_sandbox_url:
        transport: SandboxTransport = HttpSandboxTransport(settings.loom_sandbox_url)
    else:
        transport = LocalSandboxTransport(settings.loom_sandbox_root)
    calendar_transport = chosen.get("calendar") or (
        HttpCalendarTransport(settings.ical_url) if settings.ical_url else None
    )
    mail_transport = chosen.get("mail") or build_mail_transport(settings)

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

    def _fetch_events(start: datetime, end: datetime) -> list[dict]:
        if calendar_transport is None:
            raise RuntimeError(_CALENDAR_NOT_CONFIGURED)
        return parse_events(calendar_transport.fetch_ics(), start, end, ZoneInfo(settings.timezone_name))

    @tool
    def calendar_list_events(range_name: str = "today") -> str:
        """Summarize calendar events for a named range: today, tomorrow, or week (the next 7 days).

        Read-only: the ICS feed is fetched and rendered, nothing is written.
        """
        offset, days, label = _RANGES.get(range_name, (0, 0, ""))
        if not label:
            raise ValueError(f"range_name must be one of: today, tomorrow, week; got {range_name!r}")
        tz = ZoneInfo(settings.timezone_name)
        day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        start = day + timedelta(days=offset)
        return summarize(_fetch_events(start, start + timedelta(days=days)), label)

    @tool
    def calendar_summarize_range(start: str, end: str) -> str:
        """Summarize calendar events between two ISO dates or datetimes, e.g. 2026-11-01 or 2026-11-01T09:00.

        Read-only: the ICS feed is fetched and rendered, nothing is written.
        """
        tz = ZoneInfo(settings.timezone_name)
        start_dt, end_dt = _iso_boundary(start, tz, "start"), _iso_boundary(end, tz, "end")
        if end_dt < start_dt:
            raise ValueError(f"end {end!r} is before start {start!r}")
        events = _fetch_events(start_dt, end_dt)
        return summarize(events, f"{start_dt:%Y-%m-%d} to {end_dt:%Y-%m-%d}")

    @tool
    def mail_create_draft(to: str, subject: str, body: str) -> str:
        """Create an email draft in the configured IMAP drafts folder; it is never sent.

        Side effect: pauses for approval. The approval shows the full recipient,
        subject, and body before anything is stored.
        """
        if mail_transport is None:
            raise RuntimeError(MAIL_NOT_CONFIGURED)
        message = build_draft_message(to, subject, body, from_addr=settings.mail_user)
        draft_id = mail_transport.append_draft(message)
        return f"draft {draft_id} created in '{settings.mail_folder}'; it has not been sent"

    @tool
    def mail_update_draft(draft_id: str, to: str, subject: str, body: str) -> str:
        """Replace one existing draft (by id) with new content; it is never sent.

        Side effect: pauses for approval. The approval shows the full recipient,
        subject, and body before anything is stored.
        """
        if mail_transport is None:
            raise RuntimeError(MAIL_NOT_CONFIGURED)
        message = build_draft_message(to, subject, body, from_addr=settings.mail_user)
        new_id = mail_transport.replace_draft(draft_id, message)
        return f"draft {draft_id} replaced by {new_id} in '{settings.mail_folder}'"

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
        "calendar_list_events": ToolSpec(calendar_list_events, side_effect=False),
        "calendar_summarize_range": ToolSpec(calendar_summarize_range, side_effect=False),
        "mail_create_draft": ToolSpec(mail_create_draft, side_effect=True),
        "mail_update_draft": ToolSpec(mail_update_draft, side_effect=True),
    }
