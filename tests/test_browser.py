"""Browser module: the host allowlist, page rendering, and both transports."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

import pytest
from conftest import FakeBrowserTransport

from loom.browser import (
    BrowserField,
    PageState,
    PlaywrightBrowserTransport,
    _ensure_allowed,
    _parse_allowed_hosts,
    render_page_state,
)
from loom.config import Settings
from loom.tools import make_tools

URL = "http://127.0.0.1:9/form"


def form_page() -> PageState:
    return PageState(
        url=URL,
        title="Contact us",
        fields=(
            BrowserField("name", "Your name", "text", ""),
            BrowserField("email", "Email", "email", ""),
            BrowserField("topic", "Topic", "select", "general"),
            BrowserField("message", "Message", "textarea", ""),
            BrowserField("subscribe", "Subscribe to updates", "checkbox", "false"),
        ),
    )


def _settings(tmp_path, **extra) -> Settings:
    return Settings(
        loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox", **extra
    )


def test_parse_allowed_hosts_lowercases_trims_and_drops_blanks():
    assert _parse_allowed_hosts(" Example.COM , tests.dev , ") == frozenset({"example.com", "tests.dev"})
    assert _parse_allowed_hosts("") == frozenset()


def test_loopback_is_always_allowed_even_with_an_empty_list():
    _ensure_allowed("http://127.0.0.1:8000/form", frozenset())
    _ensure_allowed("http://localhost/form", frozenset())
    _ensure_allowed("http://[::1]:8000/form", frozenset())


def test_unlisted_host_raises_naming_the_rule():
    with pytest.raises(ValueError, match="LOOM_BROWSER_ALLOWED_HOSTS"):
        _ensure_allowed("http://example.com/form", frozenset())

    with pytest.raises(ValueError, match="LOOM_BROWSER_ALLOWED_HOSTS"):
        _ensure_allowed("file:///etc/passwd", frozenset())


def test_listed_host_is_allowed_regardless_of_case_or_port():
    allowed = _parse_allowed_hosts("Example.com, tests.dev")

    _ensure_allowed("http://example.com/form", allowed)
    _ensure_allowed("http://EXAMPLE.com:8443/form?q=1", allowed)
    _ensure_allowed("http://tests.dev/deep/path", allowed)


def test_render_page_state_lines_name_every_field_and_the_submit_path():
    text = render_page_state(form_page())

    assert text.splitlines() == [
        "url: http://127.0.0.1:9/form",
        "title: Contact us",
        "- name [text] 'Your name' = ''",
        "- email [email] 'Email' = ''",
        "- topic [select] 'Topic' = 'general'",
        "- message [textarea] 'Message' = ''",
        "- subscribe [checkbox] 'Subscribe to updates' = 'false'",
        "submit: browser_submit triggers the page's submit control; it waits for human approval",
    ]


def test_render_page_state_truncates_with_an_explicit_marker():
    page = PageState(
        url=URL, title="t", fields=(BrowserField("note", "Note", "textarea", "x" * 500),)
    )

    text = render_page_state(page, cap=100)

    assert "truncated at 100 characters" in text
    assert "x" * 500 not in text
    assert len(text) < 200


def test_fake_transport_open_and_fill_never_submit():
    fake = FakeBrowserTransport({URL: form_page()})

    fake.open(URL)
    state = fake.fill({"name": "Ada Lovelace"})

    assert state.fields[0].value == "Ada Lovelace"
    assert fake.submitted == [], "open and fill must never reach submit"


def test_fake_transport_fill_keeps_values_from_earlier_fills():
    fake = FakeBrowserTransport({URL: form_page()})
    fake.open(URL)

    state = fake.fill({"name": "Ada"})
    state = fake.fill({"message": "Hello"})

    values = {f.name: f.value for f in state.fields}
    assert values["name"] == "Ada" and values["message"] == "Hello"


def test_fake_transport_fill_unknown_name_lists_the_actual_fields():
    fake = FakeBrowserTransport({URL: form_page()})
    fake.open(URL)

    with pytest.raises(ValueError) as exc_info:
        fake.fill({"nickname": "Ada"})

    message = str(exc_info.value)
    assert "nickname" in message
    for actual in ("name", "email", "topic", "message", "subscribe"):
        assert actual in message


def test_fake_transport_submit_needs_an_open_page():
    fake = FakeBrowserTransport({URL: form_page()})

    with pytest.raises(RuntimeError, match="no page is open"):
        fake.submit()

    fake.open(URL)
    outcome = fake.submit()
    assert outcome.accepted is True
    assert [o.url for o in fake.submitted] == [URL]


def test_fake_transport_enforces_the_allowlist():
    listed = FakeBrowserTransport({"http://example.com/form": "Contact us"}, allowed_hosts="example.com")

    with pytest.raises(ValueError, match="LOOM_BROWSER_ALLOWED_HOSTS"):
        FakeBrowserTransport({}, allowed_hosts="example.com").open("http://unlisted.example/form")

    assert listed.open("http://example.com/form").title == "Contact us"


def test_fake_transport_plain_string_page_renders_without_fields():
    fake = FakeBrowserTransport({"http://127.0.0.1:9/plain": "Just a page"})

    state = fake.open("http://127.0.0.1:9/plain")

    assert state.title == "Just a page" and state.fields == ()


def test_pending_approval_mirrors_between_fakes_and_the_real_shape():
    fake = FakeBrowserTransport({URL: form_page()})

    assert fake.pending_approval() == {"state": "no page is open"}

    fake.open(URL)
    fake.fill({"name": "Ada"})
    assert fake.pending_approval() == {
        "url": URL,
        "fields": {"name": "Ada", "email": "", "topic": "general", "message": "", "subscribe": "false"},
    }


def _chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:  # noqa: BLE001 - any probe failure means "no browser here"
        return False


requires_chromium = pytest.mark.skipif(not _chromium_ready(), reason="playwright chromium is not installed")


class _FormSiteHandler(BaseHTTPRequestHandler):
    """Serves fixtures/web/form.html on GET and records one line per POST submission."""

    form_html = ""
    record_path = None

    def do_GET(self):
        body = self.form_html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        fields = dict(parse_qsl(self.rfile.read(length).decode("utf-8")))
        with self.record_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields) + "\n")
        body = b"<html><head><title>Done</title></head><body><h1>Submission received</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def form_server(tmp_path):
    handler = type(
        "_Handler",
        (_FormSiteHandler,),
        {
            "form_html": (Path(__file__).resolve().parents[1] / "fixtures" / "web" / "form.html").read_text(
                encoding="utf-8"
            ),
            "record_path": tmp_path / "submissions.log",
        },
    )
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/form.html", handler.record_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@requires_chromium
def test_real_transport_open_fill_submit_round_trip(tmp_path, form_server):
    base, record_path = form_server
    transport = PlaywrightBrowserTransport(_settings(tmp_path))
    try:
        state = transport.open(base)

        assert state.title == "Contact us"
        assert {f.name for f in state.fields} >= {"name", "email", "topic", "message", "subscribe"}

        filled = transport.fill(
            {
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "topic": "support",
                "message": "Hello from Loom",
                "subscribe": "true",
            }
        )
        values = {f.name: f.value for f in filled.fields}
        assert values["name"] == "Ada Lovelace" and values["subscribe"] == "true"

        outcome = transport.submit()
        assert outcome.accepted is True
        assert "Submission received" in outcome.detail
        assert outcome.url.endswith("/submit")
    finally:
        transport.close()

    submissions = record_path.read_text(encoding="utf-8").splitlines()
    assert len(submissions) == 1, "the gated submit must produce exactly one POST"
    posted = json.loads(submissions[0])
    assert posted["name"] == "Ada Lovelace" and posted["email"] == "ada@example.com"
    assert posted["topic"] == "support" and posted["message"] == "Hello from Loom"
    assert posted["subscribe"] == "on"


@requires_chromium
def test_real_transport_refuses_an_unlisted_host_before_navigating(tmp_path):
    transport = PlaywrightBrowserTransport(_settings(tmp_path, browser_allowed_hosts="example.org"))
    try:
        # A dead external host: a goto attempt would surface as RuntimeError,
        # so the ValueError proves the allowlist check ran before the browser moved.
        with pytest.raises(ValueError, match="LOOM_BROWSER_ALLOWED_HOSTS"):
            transport.open("http://dead.host.invalid/form")
    finally:
        transport.close()


def test_real_transport_pending_approval_without_a_page_needs_no_browser(tmp_path):
    transport = PlaywrightBrowserTransport(_settings(tmp_path))

    assert transport.pending_approval() == {"state": "no page is open"}


def test_browser_tools_prepare_the_page_without_submitting(tmp_path):
    fake = FakeBrowserTransport({URL: form_page()})
    registry = make_tools(_settings(tmp_path), {"browser": fake})

    opened = registry["browser_open"].func.invoke({"url": URL})
    filled = registry["browser_fill"].func.invoke({"fields": {"name": "Ada Lovelace"}})

    assert opened.splitlines()[0] == f"url: {URL}"
    assert "'Ada Lovelace'" in filled
    assert fake.submitted == [], "the prepare tools must never reach submit"


def test_browser_submit_tool_runs_the_transport_once(tmp_path):
    fake = FakeBrowserTransport({URL: form_page()})
    registry = make_tools(_settings(tmp_path), {"browser": fake})

    registry["browser_open"].func.invoke({"url": URL})
    result = registry["browser_submit"].func.invoke({})

    assert "accepted" in result and "Submission received" in result
    assert len(fake.submitted) == 1


def test_browser_submit_carries_approval_context_and_the_prepare_tools_carry_none(tmp_path):
    fake = FakeBrowserTransport({URL: form_page()})
    registry = make_tools(_settings(tmp_path), {"browser": fake})
    context = registry["browser_submit"].approval_context

    assert context is not None
    assert context() == {"state": "no page is open"}

    registry["browser_open"].func.invoke({"url": URL})
    shown = context()

    assert shown["url"] == URL and shown["fields"]["topic"] == "general"
    assert registry["browser_open"].approval_context is None
    assert registry["browser_fill"].approval_context is None
