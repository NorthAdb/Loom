"""Gate 3 demo (Phase 5): read-only calendar summaries and mail drafts, without any network or key.

Run with `.venv/Scripts/python scripts/demo_gate3_calendar_mail.py`. A local
http.server serves a fixture ICS feed, so the calendar path is a real HTTP
round trip over localhost. A scripted model drives the same LangGraph loop
chat uses, and a fake mail transport rides the same seam the IMAP transport
uses, so the draft approval flow is the real graph flow.
"""

import os
import re
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from loom.calendar import HttpCalendarTransport, parse_events, summarize
from loom.config import Settings, get_settings
from loom.graph import build_graph

TZ = ZoneInfo("Asia/Hong_Kong")

_FORBIDDEN = (
    "send_email", "sendmail", "smtplib", "send_message", "SMTP",
    "events.insert", "events.patch", "events.update", "events.delete",
)
_IMAP_VERBS = {"uid", "append", "select", "login", "logout"}


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages in order; ignores bound tools."""

    def bind_tools(self, tools, **kwargs):
        return self


class FakeMailTransport:
    """Mail transport double: records drafts instead of touching IMAP."""

    def __init__(self):
        self.appended = []
        self.replaced = []

    def append_draft(self, message_bytes):
        self.appended.append(message_bytes)
        return f"demo-draft-{len(self.appended)}"

    def replace_draft(self, old_id, message_bytes):
        self.replaced.append((old_id, message_bytes))
        return f"demo-draft-{len(self.appended) + 1}"


def tool_call_msg(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def fixture_ics() -> bytes:
    """A two-event feed: one meeting tomorrow and one all-day event the day after."""
    today = datetime.now(TZ)
    meeting = (today + timedelta(days=1)).strftime("%Y%m%d")
    offsite_start = (today + timedelta(days=2)).strftime("%Y%m%d")
    offsite_end = (today + timedelta(days=3)).strftime("%Y%m%d")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//loom//demo//EN",
        "BEGIN:VEVENT", "UID:1@loom", f"DTSTART;TZID=Asia/Hong_Kong:{meeting}T093000",
        f"DTEND;TZID=Asia/Hong_Kong:{meeting}T103000", "SUMMARY:Project sync", "END:VEVENT",
        "BEGIN:VEVENT", "UID:2@loom", f"DTSTART;VALUE=DATE:{offsite_start}", f"DTEND;VALUE=DATE:{offsite_end}",
        "SUMMARY:Writing day", "END:VEVENT",
        "END:VCALENDAR", "",
    ]
    return "\r\n".join(lines).encode("utf-8")


class ICSHandler(BaseHTTPRequestHandler):
    body = fixture_ics()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


def settings_for(data_dir: Path) -> Settings:
    return Settings(
        loom_env="dev",
        loom_data_dir=data_dir,
        loom_sandbox_root=data_dir.parent / "sandbox",
        mail_user="owner@example.com",
    )


def invoke(graph, user_text: str, thread_id: str):
    return graph.invoke(
        {"messages": [{"role": "user", "content": user_text}], "run_id": thread_id},
        {"configurable": {"thread_id": thread_id}},
    )


def scenario_calendar_summary_over_local_http(data_dir: Path, base_url: str) -> str:
    transport = HttpCalendarTransport(base_url, timeout=5)
    now = datetime.now(TZ)
    events = parse_events(transport.fetch_ics(), now, now + timedelta(days=7), TZ)
    text = summarize(events, "the next 7 days")

    print(f"  fetched {base_url}")
    print("  " + text.replace("\n", "\n  "))
    if "Project sync" in text and "Writing day" in text:
        return "PASS"
    return f"FAIL (summary={text!r})"


def scenario_calendar_tool_through_the_graph(data_dir: Path, base_url: str) -> str:
    model = ScriptedChatModel(messages=iter([
        tool_call_msg("calendar_list_events", {"range_name": "week"}),
        AIMessage(content="summarized from the tool result"),
    ]))
    graph = build_graph(
        model, settings_for(data_dir), transports={"calendar": HttpCalendarTransport(base_url, timeout=5)}
    )

    result = invoke(graph, "what's on this week?", "cal-thread")

    if "__interrupt__" in result:
        return "FAIL (a read-only tool must not stop at the gate)"
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    if not tool_messages or "Project sync" not in tool_messages[-1].content:
        return f"FAIL (tool result={tool_messages[-1].content if tool_messages else None!r})"
    print(f"  tool result: {tool_messages[-1].content.splitlines()[0]}")
    return "PASS"


def scenario_draft_approve_shows_full_text_then_appends(data_dir: Path, base_url: str) -> str:
    body = "Hi peer, let's meet on Thursday at ten to go over the Phase 5 summary. - Loom"
    mail = FakeMailTransport()
    model = ScriptedChatModel(messages=iter([
        tool_call_msg("mail_create_draft", {"to": "peer@example.com", "subject": "Meeting", "body": body}),
        AIMessage(content="draft created"),
    ]))
    graph = build_graph(model, settings_for(data_dir), transports={"mail": mail})
    config = {"configurable": {"thread_id": "approve-thread"}}

    paused = invoke(graph, "draft a mail to peer about Thursday", "approve-thread")
    if "__interrupt__" not in paused:
        return "FAIL (a side-effect draft did not stop at the gate)"
    args = paused["__interrupt__"][0].value["tools"][0]["args"]
    if "peer@example.com" not in args or "Meeting" not in args or body not in args:
        return f"FAIL (approval payload is missing the full draft text: {args!r})"
    print("  approval prompt shows: to=peer@example.com subject=Meeting body=(full text)")

    result = graph.invoke(Command(resume={"decision": "approve"}), config)
    if len(mail.appended) != 1:
        return f"FAIL (expected exactly one appended draft, got {len(mail.appended)})"
    message = mail.appended[0].decode("utf-8")
    if "peer@example.com" not in message or "Meeting" not in message:
        return "FAIL (appended draft is missing headers)"
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    if "demo-draft-1" not in tool_messages[-1].content:
        return f"FAIL (tool result does not report the draft id: {tool_messages[-1].content!r})"
    return "PASS"


def scenario_draft_reject_never_touches_the_transport(data_dir: Path, base_url: str) -> str:
    mail = FakeMailTransport()
    model = ScriptedChatModel(messages=iter([
        tool_call_msg("mail_create_draft", {"to": "peer@example.com", "subject": "Nope", "body": "should not exist"}),
    ]))
    graph = build_graph(model, settings_for(data_dir), transports={"mail": mail})
    config = {"configurable": {"thread_id": "reject-thread"}}

    paused = invoke(graph, "draft a mail", "reject-thread")
    if "__interrupt__" not in paused:
        return "FAIL (no gate stop)"
    result = graph.invoke(Command(resume={"decision": "reject"}), config)
    if mail.appended:
        return f"FAIL (reject still appended {len(mail.appended)} draft(s))"
    if "rejected" not in str(result["messages"][-1].content):
        return "FAIL (no rejection answer)"
    print("  rejected: the transport recorded no append")
    return "PASS"


def scenario_no_send_symbols_in_source() -> str:
    src = Path(__file__).resolve().parents[1] / "src" / "loom"
    text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(src.glob("*.py")))
    hits = [symbol for symbol in _FORBIDDEN if symbol in text]
    if hits:
        return f"FAIL (forbidden symbols in src/loom: {hits})"
    mail_text = (src / "mail.py").read_text(encoding="utf-8")
    verbs = set(re.findall(r"client\.(\w+)\(", mail_text))
    if not verbs <= _IMAP_VERBS:
        return f"FAIL (unexpected IMAP verbs: {sorted(verbs)})"
    print(f"  grep clean; IMAP verbs used: {sorted(verbs)}")
    return "PASS"


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="loom-gate3-mail-"))
    data_dir = root / "data"
    data_dir.mkdir()
    os.environ["LOOM_ENV"] = "dev"
    os.environ["LOOM_DATA_DIR"] = str(data_dir)
    os.environ["LOOM_MEMORY_ENABLED"] = "false"
    get_settings.cache_clear()

    server = HTTPServer(("127.0.0.1", 0), ICSHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/calendar.ics"

    scenarios = (
        ("a", "calendar summary rendered from a real local HTTP fetch of the ICS feed", scenario_calendar_summary_over_local_http),
        ("b", "calendar_list_events runs through the graph without the gate", scenario_calendar_tool_through_the_graph),
        ("c", "mail draft approve: the prompt shows the full text, then the transport appends once", scenario_draft_approve_shows_full_text_then_appends),
        ("d", "mail draft reject: the transport is never called", scenario_draft_reject_never_touches_the_transport),
        ("e", "gate guard: no send or calendar-write symbols anywhere in src/loom", lambda a, b: scenario_no_send_symbols_in_source()),
    )
    results = []
    try:
        for label, name, fn in scenarios:
            outcome = fn(data_dir, base_url)
            results.append(outcome.startswith("PASS"))
            print(f"{outcome} ({label}) {name}")
    finally:
        server.shutdown()
        server.server_close()

    summary = "all scenarios passed" if all(results) else "SCENARIOS FAILED"
    print(f"\nGate 3 calendar + mail demo: {len(results)} scenarios, {sum(results)} passed - {summary}")
    print(f"data dir: {root}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
