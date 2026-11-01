"""Gate 4 demo (Phase 6): browser fill with a human confirm before submit.

Run with `.venv/Scripts/python scripts/demo_gate4_browser.py`. A local
http.server serves fixtures/web/form.html and records every POST to /submit,
so the submit path is a real form round trip over loopback (always allowed by
the host allowlist). A scripted model drives the same LangGraph loop chat
uses; when chromium is available the real PlaywrightBrowserTransport runs,
otherwise a fake rides the same seam and the printout says which one ran.
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from loom.browser import BrowserField, PageState, PlaywrightBrowserTransport
from loom.config import Settings, get_settings
from loom.graph import build_graph

_FILL = {
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "topic": "support",
    "message": "Hello from Loom",
    "subscribe": "true",
}


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages in order; ignores bound tools."""

    def bind_tools(self, tools, **kwargs):
        return self


class DemoSubmitOutcome:
    """The submit result shape the demo's fake returns."""

    def __init__(self, url: str):
        self.url = url
        self.accepted = True
        self.detail = "Submission received"


class RecordingFakeBrowserTransport:
    """Fake fallback: prebuilt page, submissions mirrored into the recorder file."""

    def __init__(self, base: str, record_path: Path):
        self.current = PageState(
            url=base,
            title="Contact us",
            fields=(
                BrowserField("name", "Your name", "text", ""),
                BrowserField("email", "Email", "email", ""),
                BrowserField("topic", "Topic", "select", "general"),
                BrowserField("message", "Message", "textarea", ""),
                BrowserField("subscribe", "Subscribe to updates", "checkbox", "false"),
            ),
        )
        self._record_path = record_path

    def open(self, url: str) -> PageState:
        self.current = PageState(url=url, title=self.current.title, fields=self.current.fields)
        return self.current

    def fill(self, fields: dict) -> PageState:
        self.current = PageState(
            url=self.current.url,
            title=self.current.title,
            fields=tuple(
                BrowserField(f.name, f.label, f.kind, str(fields.get(f.name, f.value)))
                for f in self.current.fields
            ),
        )
        return self.current

    def submit(self) -> DemoSubmitOutcome:
        with self._record_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"fields": {f.name: f.value for f in self.current.fields}}) + "\n")
        return DemoSubmitOutcome(self.current.url)

    def pending_approval(self) -> dict:
        return {"url": self.current.url, "fields": {f.name: f.value for f in self.current.fields}}


def tool_call_msg(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:  # noqa: BLE001 - any probe failure just means "no browser here"
        return False


def make_handler(record_path: Path, form_html: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = form_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            fields = dict(parse_qsl(self.rfile.read(length).decode("utf-8")))
            with record_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(fields) + "\n")
            body = b"<html><head><title>Done</title></head><body><h1>Submission received</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def settings_for(data_dir: Path) -> Settings:
    return Settings(
        loom_env="dev",
        loom_data_dir=data_dir,
        loom_sandbox_root=data_dir.parent / "sandbox",
        mail_user="owner@example.com",
        # The demo form is loopback, which the allowlist always admits; the
        # listed entry shows a populated LOOM_BROWSER_ALLOWED_HOSTS anyway.
        browser_allowed_hosts="localhost",
    )


def invoke(graph, user_text: str, thread_id: str):
    return graph.invoke(
        {"messages": [{"role": "user", "content": user_text}], "run_id": thread_id},
        {"configurable": {"thread_id": thread_id}},
    )


def recorder_lines(record_path: Path) -> list[str]:
    if not record_path.exists():
        return []
    return record_path.read_text(encoding="utf-8").splitlines()


def print_audit(data_dir: Path) -> None:
    for path in sorted((data_dir / "audit").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            print(f"  audit: {line}")


def prepare_scripted(base: str) -> list:
    return [
        tool_call_msg("browser_open", {"url": base}),
        tool_call_msg("browser_fill", {"fields": dict(_FILL)}),
    ]


def scenario_open_and_fill(settings, transport, base: str, record_path: Path) -> str:
    model = ScriptedChatModel(messages=iter([*prepare_scripted(base), AIMessage(content="page filled")]))
    graph = build_graph(model, settings, transports={"browser": transport})

    result = invoke(graph, "fill the contact form", "fill-thread")

    if "__interrupt__" in result:
        return "FAIL (open and fill must not stop at the gate)"
    if recorder_lines(record_path):
        return f"FAIL (open and fill submitted {len(recorder_lines(record_path))} time(s))"
    renders = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    if not renders or "Ada Lovelace" not in renders[-1].content:
        return "FAIL (the filled value is missing from the rendered page state)"
    print("  page state after fill (what the model sees):")
    print("  " + renders[-1].content.replace("\n", "\n  "))
    return "PASS"


def scenario_submit_approve(settings, transport, base: str, record_path: Path, data_dir: Path) -> str:
    model = ScriptedChatModel(
        messages=iter([*prepare_scripted(base), tool_call_msg("browser_submit", {}), AIMessage(content="sent")])
    )
    graph = build_graph(model, settings, transports={"browser": transport})
    config = {"configurable": {"thread_id": "approve-thread"}}

    paused = invoke(graph, "send the contact form", "approve-thread")
    if "__interrupt__" not in paused:
        return "FAIL (browser_submit did not stop at the gate)"
    shown = json.loads(paused["__interrupt__"][0].value["tools"][0]["args"])
    if shown.get("fields", {}).get("name") != _FILL["name"]:
        return f"FAIL (approval payload is missing the filled values: {shown!r})"
    print(f"  approval prompt shows: url={shown.get('url')} fields={json.dumps(shown.get('fields'), ensure_ascii=False)}")

    result = graph.invoke(Command(resume={"decision": "approve"}), config)
    lines = recorder_lines(record_path)
    if len(lines) != 1:
        return f"FAIL (expected exactly one submission, got {len(lines)})"
    print(f"  recorder ({len(lines)} submission): {lines[0]}")
    print_audit(data_dir)
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    if not tool_messages or "accepted" not in tool_messages[-1].content:
        return "FAIL (the submit result does not read as accepted)"
    return "PASS"


def scenario_submit_reject(settings, transport, base: str, record_path: Path, data_dir: Path) -> str:
    model = ScriptedChatModel(
        messages=iter([*prepare_scripted(base), tool_call_msg("browser_submit", {})])
    )
    graph = build_graph(model, settings, transports={"browser": transport})
    config = {"configurable": {"thread_id": "reject-thread"}}

    paused = invoke(graph, "send the contact form again", "reject-thread")
    if "__interrupt__" not in paused:
        return "FAIL (no gate stop on the second submit)"
    result = graph.invoke(Command(resume={"decision": "reject"}), config)

    lines = recorder_lines(record_path)
    if len(lines) != 1:
        return f"FAIL (the rejected submit changed the recorder to {len(lines)} submission(s))"
    final = str(result["messages"][-1].content)
    if "rejected" not in final:
        return "FAIL (no rejection answer)"
    print(f"  aborted reply: {final}")
    print_audit(data_dir)
    return "PASS"


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="loom-gate4-browser-"))
    data_dir = root / "data"
    data_dir.mkdir()
    os.environ["LOOM_ENV"] = "dev"
    os.environ["LOOM_DATA_DIR"] = str(data_dir)
    os.environ["LOOM_MEMORY_ENABLED"] = "false"
    get_settings.cache_clear()

    record_path = root / "submissions.jsonl"
    form_html = (Path(__file__).resolve().parents[1] / "fixtures" / "web" / "form.html").read_text(encoding="utf-8")
    server = HTTPServer(("127.0.0.1", 0), make_handler(record_path, form_html))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/form.html"

    settings = settings_for(data_dir)
    if chromium_ready():
        transport = PlaywrightBrowserTransport(settings)
        note = "real headless chromium"
    else:
        transport = RecordingFakeBrowserTransport(base, record_path)
        note = "playwright chromium unavailable, fake transport"
    print(f"transport: {type(transport).__name__} ({note})")

    scenarios = (
        ("a", "browser_open + browser_fill prepare the page with no approval and no submit", lambda: scenario_open_and_fill(settings, transport, base, record_path)),
        ("b", "browser_submit stops at the gate; the human approves and exactly one POST lands", lambda: scenario_submit_approve(settings, transport, base, record_path, data_dir)),
        ("c", "a second submit rejected by the human leaves the recorder at one submission", lambda: scenario_submit_reject(settings, transport, base, record_path, data_dir)),
    )
    results = []
    try:
        for label, name, fn in scenarios:
            outcome = fn()
            results.append(outcome.startswith("PASS"))
            print(f"{outcome} ({label}) {name}")
    finally:
        server.shutdown()
        server.server_close()
        if isinstance(transport, PlaywrightBrowserTransport):
            transport.close()

    summary = "all scenarios passed" if all(results) else "SCENARIOS FAILED"
    print(f"\nGate 4 browser demo: {len(results)} scenarios, {sum(results)} passed - {summary}")
    print(f"data dir: {root}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
