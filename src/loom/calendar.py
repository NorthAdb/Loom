"""Read-only calendar: fetch an ICS feed, parse events, and summarize.

Stdlib plus icalendar only. `parse_events` clamps events to a window, expands
simple RRULEs with a hard occurrence cap, and skips events it cannot understand
instead of failing the whole feed. There is no create, update, or delete
anything in this module: the only network call is a GET of the feed.
"""

import logging
import urllib.request
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from icalendar import Calendar

logger = logging.getLogger("loom")

# One rule yields at most this many occurrences into the returned window, and
# the scan stops after this many enumerated occurrences even if the window is
# never reached, so a pathological rule cannot spin the parser.
_WINDOW_OCCURRENCE_CAP = 200
_ENUMERATION_CAP = 100_000

# A feed larger than this is refused mid-read, so a bad URL cannot pull the
# whole response into memory.
_MAX_FEED_BYTES = 1024 * 1024
_READ_CHUNK = 64 * 1024


class CalendarTransport(Protocol):
    """How the calendar tools reach an ICS feed."""

    def fetch_ics(self) -> bytes: ...


def redact_url(url: str) -> str:
    """The URL for messages: scheme and host only, with an explicit truncation mark.

    The path and query often carry the feed's private token, so they are
    always dropped; userinfo is dropped with them.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if not parts.scheme or not host:
            return "<unparseable url>"
        if parts.port:
            host = f"{host}:{parts.port}"
        return f"{parts.scheme}://{host}/…"
    except ValueError:
        return "<unparseable url>"


def _is_loopback(host: str) -> bool:
    return host == "localhost" or host == "::1" or host.startswith("127.")


class _HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse any redirect that would not land on https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(str(newurl)).scheme != "https":
            raise ValueError(f"refusing redirect to a non-https URL ({redact_url(str(newurl))})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpCalendarTransport:
    """GETs the ICS feed over https; every failure becomes one redacted RuntimeError.

    https is required because these feed URLs carry a private token in the
    path or query; plain http is accepted only for loopback hosts, which never
    leaves the machine (tests and demos serve fixtures that way). The original
    exception is never chained: it can embed the raw URL and its credentials.
    """

    def __init__(self, url: str, timeout: float = 15.0) -> None:
        self._url = url
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_HTTPSOnlyRedirectHandler())

    def fetch_ics(self) -> bytes:
        try:
            return self._fetch()
        except Exception as exc:  # noqa: BLE001 - one redacted failure shape for every cause
            raise RuntimeError(f"calendar feed fetch failed: {type(exc).__name__} ({redact_url(self._url)})") from None

    def _fetch(self) -> bytes:
        parts = urlsplit(self._url)
        if parts.scheme != "https" and not (parts.scheme == "http" and _is_loopback(parts.hostname or "")):
            raise ValueError("only https calendar feeds are supported")
        with self._opener.open(self._url, timeout=self._timeout) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(_READ_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_FEED_BYTES:
                    raise ValueError(f"calendar feed exceeds the {_MAX_FEED_BYTES // 1024} KiB response cap")
                chunks.append(chunk)
            return b"".join(chunks)


def _to_tz(prop, tz: ZoneInfo) -> tuple[datetime, bool]:
    """One DTSTART/DTEND property value -> (aware datetime in tz, all_day).

    Handles date-only values (all day), aware datetimes (UTC Z suffix or an
    icalendar-resolved TZID), and naive datetimes (floating; TZID param if
    given, else the configured timezone).
    """
    value = prop.dt
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=tz), True
    if value.tzinfo is not None:
        return value.astimezone(tz), False
    zone = tz
    tzid = prop.params.get("TZID")
    if tzid:
        zone = ZoneInfo(str(tzid))
    return value.replace(tzinfo=zone), False


def _duration(event, start: datetime, all_day: bool, tz: ZoneInfo) -> timedelta:
    """The event duration: DTEND (exclusive for all-day), else DURATION, else a day or zero.

    All-day events whose producer wrote DTEND equal to DTSTART still render as
    one day, not a zero-length range.
    """
    dtend = event.get("DTEND")
    if dtend is not None:
        end, _ = _to_tz(dtend, tz)
        duration = end - start
        if all_day and duration <= timedelta(0):
            return timedelta(days=1)
        return max(duration, timedelta(0))
    try:
        return max(event.decoded("duration"), timedelta(0))
    except (KeyError, TypeError, ValueError):
        return timedelta(days=1) if all_day else timedelta(0)


def _excluded(event, fallback_tz) -> tuple[set[float], set]:
    """EXDATE values as (UTC timestamps, local dates) so occurrences compare either way.

    Date-only EXDATEs (the usual form on recurring events) exclude by calendar day
    in the event's own zone.
    """
    try:
        exdates = event.exdates or []
    except Exception:  # noqa: BLE001 - a broken EXDATE must not kill the feed
        return set(), set()
    stamps: set[float] = set()
    days: set = set()
    for value in exdates:
        if isinstance(value, datetime) and value.tzinfo is not None:
            stamps.add(value.astimezone(UTC).timestamp())
        elif isinstance(value, date):
            days.add(value)
    return stamps, days


def _occurrence_starts(event, start: datetime, window_start: datetime, window_end: datetime) -> list[datetime]:
    """Occurrence starts inside the window, or [start] when there is no usable RRULE."""
    try:
        rules = event.rrules or []
    except Exception:  # noqa: BLE001 - a broken RRULE must not kill the feed
        rules = []
    if not rules:
        return [start]
    try:
        rule = rrulestr(rules[0].to_ical().decode(), dtstart=start)
    except (TypeError, ValueError) as exc:
        logger.warning("calendar: unsupported RRULE; keeping the first occurrence only", extra={"error": str(exc)})
        return [start]
    excluded, excluded_days = _excluded(event, window_start.tzinfo)
    starts: list[datetime] = []
    for scanned, occ in enumerate(rule, start=1):
        if occ > window_end or scanned > _ENUMERATION_CAP:
            break
        if occ >= window_start and occ.astimezone(UTC).timestamp() not in excluded and occ.date() not in excluded_days:
            starts.append(occ)
            if len(starts) >= _WINDOW_OCCURRENCE_CAP:
                logger.warning("calendar: RRULE hit the window occurrence cap", extra={"cap": _WINDOW_OCCURRENCE_CAP})
                break
    return starts


def _parse_event(event, window_start: datetime, window_end: datetime, tz: ZoneInfo) -> list[dict]:
    """One VEVENT -> zero or more event dicts; [] when it lies outside the window."""
    start_prop = event.get("DTSTART")
    if start_prop is None:
        raise ValueError("event has no DTSTART")
    start, all_day = _to_tz(start_prop, tz)
    duration = _duration(event, start, all_day, tz)
    summary = str(event.get("SUMMARY", "(no title)"))
    out = []
    for occ in _occurrence_starts(event, start, window_start, window_end):
        if occ > window_end or occ + duration < window_start:
            continue
        out.append({"start": occ, "end": occ + duration, "summary": summary, "all_day": all_day})
    return out


def parse_events(ics_bytes: bytes, start: datetime, end: datetime, tz: ZoneInfo) -> list[dict]:
    """Parse VEVENTs overlapping [start, end], expand simple RRULEs, sorted by start.

    Events with no readable start are skipped (counted in a warning) rather than
    failing the feed; each returned dict has start, end, summary, all_day, with
    datetimes in `tz`.
    """
    calendar = Calendar.from_ical(ics_bytes)
    events: list[dict] = []
    skipped = 0
    for event in calendar.walk("VEVENT"):
        try:
            events.extend(_parse_event(event, start, end, tz))
        except Exception as exc:  # noqa: BLE001 - one malformed event must not fail the feed
            skipped += 1
            logger.warning("calendar: skipped a malformed event", extra={"error": str(exc)})
    if skipped:
        logger.warning("calendar: skipped malformed events", extra={"skipped": skipped})
    events.sort(key=lambda item: (item["start"], item["summary"]))
    return events


def summarize(events: list[dict], range_label: str) -> str:
    """Render events as one friendly line per event with local times."""
    if not events:
        return f"Nothing scheduled for {range_label}."
    lines = [f"{range_label}: {len(events)} event(s)"]
    for event in events:
        start: datetime = event["start"]
        end: datetime = event["end"]
        if event["all_day"]:
            last = (end - timedelta(days=1)).date()
            day = f"{start:%Y-%m-%d}" if last == start.date() else f"{start:%Y-%m-%d} to {last:%Y-%m-%d}"
            lines.append(f"- {day} all day: {event['summary']}")
        elif start.date() == end.date():
            lines.append(f"- {start:%Y-%m-%d %H:%M}-{end:%H:%M} {event['summary']}")
        else:
            lines.append(f"- {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} {event['summary']}")
    return "\n".join(lines)
