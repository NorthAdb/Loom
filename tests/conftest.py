import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from loom.config import get_settings


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages in order; bind_tools returns the model itself."""

    def bind_tools(self, tools, **kwargs):
        return self


def tool_call_msg(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    """Build an AIMessage carrying one tool call, as a model would emit."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def read_audit(data_dir) -> list[dict]:
    """Parse the audit JSONL a test just wrote."""
    path = next((data_dir / "audit").glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class FakeMailTransport:
    """Mail transport double: records drafts instead of touching IMAP."""

    def __init__(self):
        self.appended = []
        self.replaced = []

    def append_draft(self, message_bytes):
        self.appended.append(message_bytes)
        return f"fake-draft-{len(self.appended)}"

    def replace_draft(self, old_id, message_bytes):
        self.replaced.append((old_id, message_bytes))
        return f"fake-draft-{len(self.appended) + 1}"


class FakeCalendarTransport:
    """Calendar transport double: serves a fixed ICS body."""

    def __init__(self, ics: bytes):
        self.ics = ics
        self.fetches = 0

    def fetch_ics(self) -> bytes:
        self.fetches += 1
        return self.ics


# A real ICS feed body: a TZID event, a UTC (Z) event, an all-day event, a
# weekly RRULE with one EXDATE, an event far outside the window, and one
# malformed event with no dates at all.
CALENDAR_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//loom//test//EN
BEGIN:VEVENT
UID:1@loom
DTSTART;TZID=Asia/Hong_Kong:20261102T093000
DTEND;TZID=Asia/Hong_Kong:20261102T103000
SUMMARY:Standup
END:VEVENT
BEGIN:VEVENT
UID:2@loom
DTSTART:20261103T010000Z
DTEND:20261103T020000Z
SUMMARY:Sync with the other zone
END:VEVENT
BEGIN:VEVENT
UID:3@loom
DTSTART;VALUE=DATE:20261104
DTEND;VALUE=DATE:20261105
SUMMARY:Team offsite
END:VEVENT
BEGIN:VEVENT
UID:4@loom
DTSTART;TZID=Asia/Hong_Kong:20261026T100000
DTEND;TZID=Asia/Hong_Kong:20261026T110000
SUMMARY:Weekly review
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=6
EXDATE;TZID=Asia/Hong_Kong:20261109T100000
END:VEVENT
BEGIN:VEVENT
UID:5@loom
DTSTART;TZID=Asia/Hong_Kong:20261225T180000
DTEND;TZID=Asia/Hong_Kong:20261225T200000
SUMMARY:Far outside any test window
END:VEVENT
BEGIN:VEVENT
UID:6@loom
SUMMARY:malformed event with no dates
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Isolate tests from the host environment and the repository directory."""
    get_settings.cache_clear()
    for name in (
        "LOOM_ENV", "LOOM_LOG_LEVEL", "LOOM_DATA_DIR", "LOOM_SANDBOX_ROOT", "LOOM_SANDBOX_URL",
        "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL",
        "LOOM_MEMORY_ENABLED", "LOOM_MEMORY_INJECT_LIMIT", "MEM0_TELEMETRY",
        "LOOM_ICAL_URL", "LOOM_IMAP_HOST", "LOOM_IMAP_PORT", "LOOM_MAIL_USER",
        "LOOM_MAIL_PASSWORD", "LOOM_MAIL_FOLDER", "LOOM_TIMEZONE",
    ):
        monkeypatch.delenv(name, raising=False)
    # Memory tests inject fakes through the seams; everything else runs with the
    # semantic layer off so no test needs an API key or a Mem0 endpoint.
    monkeypatch.setenv("LOOM_MEMORY_ENABLED", "false")
    monkeypatch.chdir(tmp_path)
    yield
    get_settings.cache_clear()
