"""Calendar module: parsing, expansion, rendering, and the HTTP transport."""

import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import pytest
from conftest import CALENDAR_ICS, FakeCalendarTransport

from loom.calendar import HttpCalendarTransport, parse_events, summarize
from loom.config import Settings
from loom.tools import make_tools

TZ = ZoneInfo("Asia/Hong_Kong")
WINDOW_START = datetime(2026, 11, 1, tzinfo=TZ)
WINDOW_END = datetime(2026, 11, 23, tzinfo=TZ)


def parse_window(ics=CALENDAR_ICS):
    return parse_events(ics, WINDOW_START, WINDOW_END, TZ)


def test_parse_events_handles_tzid_utc_all_day_and_skips_malformed():
    events = parse_window()

    assert [event["summary"] for event in events] == [
        "Standup", "Weekly review", "Sync with the other zone", "Team offsite", "Weekly review",
    ]
    standup, _first_review, sync, offsite, second_review = events

    assert standup["start"] == datetime(2026, 11, 2, 9, 30, tzinfo=TZ)
    assert standup["end"] == datetime(2026, 11, 2, 10, 30, tzinfo=TZ)
    assert standup["all_day"] is False

    assert sync["start"] == datetime(2026, 11, 3, 9, 0, tzinfo=TZ), "01:00Z is 09:00 HKT"

    assert offsite["all_day"] is True
    assert offsite["start"] == datetime(2026, 11, 4, 0, 0, tzinfo=TZ)
    assert offsite["end"] == datetime(2026, 11, 5, 0, 0, tzinfo=TZ), "all-day DTEND is exclusive"

    assert second_review["start"] == datetime(2026, 11, 16, 10, 0, tzinfo=TZ)


def test_rrule_expands_within_the_window_and_honors_exdate():
    events = parse_window()

    reviews = [event for event in events if event["summary"] == "Weekly review"]

    assert [event["start"] for event in reviews] == [
        datetime(2026, 11, 2, 10, 0, tzinfo=TZ),
        datetime(2026, 11, 16, 10, 0, tzinfo=TZ),  # Nov 9 is EXDATEd
    ]


def test_events_outside_the_window_are_excluded():
    assert all("Far outside" not in event["summary"] for event in parse_window())


def test_summarize_renders_one_line_per_event_with_local_times():
    events = parse_window()

    text = summarize(events, "the window")

    assert text.splitlines() == [
        "the window: 5 event(s)",
        "- 2026-11-02 09:30-10:30 Standup",
        "- 2026-11-02 10:00-11:00 Weekly review",
        "- 2026-11-03 09:00-10:00 Sync with the other zone",
        "- 2026-11-04 all day: Team offsite",
        "- 2026-11-16 10:00-11:00 Weekly review",
    ]


def test_summarize_empty_range_is_friendly():
    assert summarize([], "today") == "Nothing scheduled for today."


def test_degenerate_all_day_event_still_renders_one_day():
    ics = (
        b"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//loom//test//EN\n"
        b"BEGIN:VEVENT\nUID:x@loom\nDTSTART;VALUE=DATE:20261104\nDTEND;VALUE=DATE:20261104\n"
        b"SUMMARY:Zero-length all day\nEND:VEVENT\nEND:VCALENDAR\n"
    )

    events = parse_events(ics, WINDOW_START, WINDOW_END, TZ)

    assert events[0]["all_day"] is True
    assert "- 2026-11-04 all day: Zero-length all day" in summarize(events, "week")


class _ICSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/missing.ics":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/denied.ics":
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.end_headers()
        self.wfile.write(CALENDAR_ICS)

    def log_message(self, *args):
        pass


@pytest.fixture
def ics_server():
    server = HTTPServer(("127.0.0.1", 0), _ICSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/calendar.ics"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_http_transport_fetches_a_real_ics_round_trip(ics_server):
    transport = HttpCalendarTransport(ics_server, timeout=5)

    body = transport.fetch_ics()

    assert b"BEGIN:VCALENDAR" in body
    assert "Standup" in summarize(parse_events(body, WINDOW_START, WINDOW_END, TZ), "feed")


def test_http_transport_http_error_is_readable(ics_server):
    transport = HttpCalendarTransport(ics_server.rsplit("/", 1)[0] + "/missing.ics", timeout=5)

    with pytest.raises(RuntimeError, match="calendar feed fetch failed"):
        transport.fetch_ics()


def test_http_transport_error_message_never_carries_url_credentials(ics_server):
    host = ics_server.rsplit("/", 1)[0]
    transport = HttpCalendarTransport(f"http://user:super-secret@{host.split('//', 1)[1]}/denied.ics", timeout=5)

    with pytest.raises(RuntimeError) as exc_info:
        transport.fetch_ics()

    message = str(exc_info.value)
    assert "super-secret" not in message and "user@" not in message
    assert host in message, "the readable message still names the redacted URL"


def test_fetch_ics_error_has_no_cause_chain():
    transport = HttpCalendarTransport("http://127.0.0.1:1/private-token.ics", timeout=2)

    with pytest.raises(RuntimeError, match="calendar feed fetch failed") as exc_info:
        transport.fetch_ics()

    assert exc_info.value.__cause__ is None, "the chained exception can carry the raw URL"
    assert exc_info.value.__suppress_context__ is True
    assert "private-token" not in str(exc_info.value)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/ics.ics", "http://example.com/private-cbd8f2basic.ics"],
)
def test_fetch_ics_rejects_non_https(url):
    transport = HttpCalendarTransport(url, timeout=2)

    with pytest.raises(RuntimeError, match="calendar feed fetch failed.*ValueError"):
        transport.fetch_ics()


def test_fetch_ics_caps_response_body():
    class _LargeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * (1024 * 1024 + 16))

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _LargeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        transport = HttpCalendarTransport(f"http://127.0.0.1:{server.server_address[1]}/big.ics", timeout=5)

        with pytest.raises(RuntimeError, match="calendar feed fetch failed"):
            transport.fetch_ics()
    finally:
        server.shutdown()
        server.server_close()


def test_tool_error_never_contains_ics_secret(tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    transport = HttpCalendarTransport("http://127.0.0.1:1/ics?token=supersecret1", timeout=2)
    registry = make_tools(settings, {"calendar": transport})

    with pytest.raises(RuntimeError) as exc_info:
        registry["calendar_list_events"].func.invoke({"range_name": "today"})

    assert "supersecret1" not in str(exc_info.value)
    assert "127.0.0.1:1/…" in str(exc_info.value)


def _ics_at(offsets):
    """Build an ICS with one 09:30-10:00 event per (day offset, title)."""
    now = datetime.now(TZ)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//loom//test//EN"]
    for offset, title in offsets:
        start = (now + timedelta(days=offset)).strftime("%Y%m%d") + "T093000"
        end = (now + timedelta(days=offset)).strftime("%Y%m%d") + "T100000"
        lines += [
            "BEGIN:VEVENT", f"UID:{offset}@loom", f"DTSTART;TZID=Asia/Hong_Kong:{start}",
            f"DTEND;TZID=Asia/Hong_Kong:{end}", f"SUMMARY:{title}", "END:VEVENT",
        ]
    return ("\r\n".join(lines + ["END:VCALENDAR", ""])).encode("utf-8")


def make_registry(tmp_path, ics):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    return make_tools(settings, {"calendar": FakeCalendarTransport(ics)})


def test_calendar_list_events_tool_covers_tomorrow_and_week(tmp_path):
    registry = make_registry(
        tmp_path,
        _ics_at([(1, "tomorrow thing"), (5, "in week"), (8, "beyond week")]),
    )

    tomorrow = registry["calendar_list_events"].func.invoke({"range_name": "tomorrow"})
    week = registry["calendar_list_events"].func.invoke({"range_name": "week"})

    assert "tomorrow thing" in tomorrow and "in week" not in tomorrow
    assert "in week" in week and "tomorrow thing" in week
    assert "beyond week" not in week


def test_calendar_list_events_rejects_unknown_range(tmp_path):
    registry = make_registry(tmp_path, _ics_at([(0, "x")]))

    with pytest.raises(ValueError, match="today, tomorrow, week"):
        registry["calendar_list_events"].func.invoke({"range_name": "fortnight"})


def test_calendar_summarize_range_tool_parses_iso_arguments(tmp_path):
    registry = make_registry(tmp_path, CALENDAR_ICS)

    text = registry["calendar_summarize_range"].func.invoke({"start": "2026-11-01", "end": "2026-11-23"})

    assert "Standup" in text and "Team offsite" in text


def test_calendar_summarize_range_rejects_inverted_range(tmp_path):
    registry = make_registry(tmp_path, CALENDAR_ICS)

    with pytest.raises(ValueError, match="before start"):
        registry["calendar_summarize_range"].func.invoke({"start": "2026-11-23", "end": "2026-11-01"})


def test_unconfigured_calendar_tool_raises_a_config_error(tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    registry = make_tools(settings)

    with pytest.raises(RuntimeError, match="LOOM_ICAL_URL"):
        registry["calendar_list_events"].func.invoke({})


ICS_DATE_EXDATE = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//loom//test//EN\r
BEGIN:VEVENT\r
UID:exdate@loom\r
DTSTAMP:20261031T000000Z\r
DTSTART;TZID=Asia/Hong_Kong:20261102T100000\r
DTEND;TZID=Asia/Hong_Kong:20261102T110000\r
SUMMARY:Standup\r
RRULE:FREQ=DAILY;COUNT=5\r
EXDATE;VALUE=DATE:20261104\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_date_only_exdate_excludes_that_occurrence(tmp_path):
    tz = ZoneInfo("Asia/Hong_Kong")
    start = datetime(2026, 11, 2, tzinfo=tz)
    end = datetime(2026, 11, 7, tzinfo=tz)

    events = parse_events(ICS_DATE_EXDATE.encode(), start, end, tz)

    summaries = [event["summary"] for event in events]
    assert summaries.count("Standup") == 4
    assert all(event["start"].date() != date(2026, 11, 4) for event in events)
