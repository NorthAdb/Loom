"""Browser module: the host allowlist, page rendering, and both transports."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
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
OFFSITE = "http://offsite.invalid/collect"


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


class _PageSiteHandler(BaseHTTPRequestHandler):
    """Serves per-path pages (and optional 302 redirects) and records every POST body."""

    pages: ClassVar[dict] = {}
    redirects: ClassVar[dict] = {}
    record_path = None

    def do_GET(self):
        target = self.redirects.get(self.path)
        if target is not None:
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = self.pages.get(self.path, "<html><body><h1>not found</h1></body></html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        fields = dict(parse_qsl(self.rfile.read(length).decode("utf-8")))
        with self.record_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"path": self.path, "fields": fields}) + "\n")
        body = b"<html><head><title>Done</title></head><body><h1>Submission received</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def page_site(tmp_path):
    """An in-thread site whose pages and redirects each test fills in; POSTs land in a file."""
    site = SimpleNamespace(pages={}, redirects={}, record_path=tmp_path / "posts.log", base="")
    handler = type(
        "_PageSite",
        (_PageSiteHandler,),
        {"pages": site.pages, "redirects": site.redirects, "record_path": site.record_path},
    )
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    site.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield site
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _posted(page_site) -> list[dict]:
    if not page_site.record_path.exists():
        return []
    return [json.loads(line) for line in page_site.record_path.read_text(encoding="utf-8").splitlines()]


@requires_chromium
def test_oninput_self_submit_to_offsite_host_never_posts(tmp_path, page_site, caplog):
    page_site.pages["/tricky.html"] = (
        "<html><head><title>Tricky</title></head><body>"
        f'<form action="{OFFSITE}" method="post">'
        '<input id="name" name="name" oninput="this.form.submit()">'
        '<button type="submit">Go</button></form></body></html>'
    )
    transport = PlaywrightBrowserTransport(_settings(tmp_path))
    try:
        with caplog.at_level(logging.DEBUG):
            transport.open(page_site.base + "/tricky.html")
            render = render_page_state(transport.fill({"name": "Ada Lovelace"}))
    finally:
        transport.close()

    assert _posted(page_site) == [], "page scripts must not send a POST off the allowlist during fill"
    assert transport._blocked == [OFFSITE], "the firewall refuses the request the page script started"
    assert "offsite.invalid" not in render, "no off-site content reaches the model"
    blocked_logs = [record for record in caplog.records if "host allowlist" in record.getMessage()]
    assert blocked_logs, "every blocked request is logged as a warning"
    for record in blocked_logs:
        assert "offsite.invalid" in record.host and "/collect" not in record.host, "the log names scheme and host only"


@requires_chromium
def test_onload_self_submit_to_offsite_host_never_posts(tmp_path, page_site):
    page_site.pages["/auto.html"] = (
        "<html><head><title>Auto</title></head><body>"
        f'<form action="{OFFSITE}" method="post"><input name="n" value="v"></form>'
        "<script>window.onload = () => document.forms[0].submit()</script>"
        "</body></html>"
    )
    transport = PlaywrightBrowserTransport(_settings(tmp_path))
    try:
        state = transport.open(page_site.base + "/auto.html")
    finally:
        transport.close()

    assert _posted(page_site) == [], "the page's own load handler must not submit off the allowlist"
    assert state.url == page_site.base + "/auto.html", "the page stays on the allowed origin"
    assert transport._blocked == [OFFSITE], "the firewall aborts the scripted submit"


@requires_chromium
def test_redirect_to_offsite_is_aborted_page_never_lands_offsite(tmp_path, page_site):
    page_site.redirects["/away"] = "http://offsite.invalid/x"
    transport = PlaywrightBrowserTransport(_settings(tmp_path))
    try:
        with pytest.raises(RuntimeError) as exc_info:
            transport.open(page_site.base + "/away")
    finally:
        transport.close()

    assert transport._page is None, "a failed navigation leaves no usable page behind"
    assert transport._blocked == ["http://offsite.invalid/x"], "the firewall aborted the redirect target before send"
    assert _posted(page_site) == []
    message = str(exc_info.value)
    assert "http://offsite.invalid/…" in message and "/x" not in message, "the failure names the redacted url only, never an off-site body"


@requires_chromium
def test_submit_refuses_a_formaction_outside_the_allowlist_before_clicking(tmp_path, page_site):
    page_site.pages["/formaction.html"] = (
        "<html><head><title>Formaction</title></head><body>"
        f'<form action="/record" method="post"><input name="n" value="v">'
        f'<button type="submit" formaction="{OFFSITE}">Go</button></form></body></html>'
    )
    transport = PlaywrightBrowserTransport(_settings(tmp_path))
    try:
        transport.open(page_site.base + "/formaction.html")
        with pytest.raises(ValueError, match="LOOM_BROWSER_ALLOWED_HOSTS"):
            transport.submit()
    finally:
        transport.close()

    assert _posted(page_site) == [], "a refused submission must never be clicked"


@requires_chromium
def test_submit_uses_the_form_owning_the_clicked_button(tmp_path, page_site):
    page_site.pages["/two-forms.html"] = (
        "<html><head><title>Two forms</title></head><body>"
        f'<form action="{OFFSITE}" method="post"><input name="a" value="1"></form>'
        '<form action="/record" method="post"><input name="b" value="2">'
        '<button type="submit">Go</button></form></body></html>'
    )
    transport = PlaywrightBrowserTransport(_settings(tmp_path))
    try:
        transport.open(page_site.base + "/two-forms.html")
        outcome = transport.submit()
    finally:
        transport.close()

    posts = _posted(page_site)
    assert [post["path"] for post in posts] == ["/record"], "the click submits the form that owns the button"
    assert outcome.accepted is True


@requires_chromium
def test_onsubmit_action_rewrite_is_blocked_and_never_accepted(tmp_path, page_site):
    page_site.pages["/rewrite.html"] = (
        "<html><head><title>Rewrite</title></head><body>"
        f"<form action=\"/record\" method=\"post\" onsubmit=\"this.action='{OFFSITE}'\">"
        '<input name="n" value="v"><button type="submit">Go</button></form></body></html>'
    )
    transport = PlaywrightBrowserTransport(_settings(tmp_path))
    try:
        transport.open(page_site.base + "/rewrite.html")
        with pytest.raises(RuntimeError, match="blocked by the host allowlist"):
            transport.submit()
        assert transport._blocked == [OFFSITE], "the firewall refused the rewritten submission"
    finally:
        transport.close()

    assert _posted(page_site) == [], "a blocked submission sends nothing"


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
