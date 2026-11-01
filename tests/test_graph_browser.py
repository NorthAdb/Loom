"""The graph with browser tools: open and fill run free, submit waits at the gate."""

import json

from conftest import FakeBrowserTransport, ScriptedChatModel, read_audit, tool_call_msg
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from loom.browser import BrowserField, PageState
from loom.config import Settings
from loom.graph import _APPROVAL_SIZE_LIMIT, build_graph

URL = "http://127.0.0.1:9/form"


def form_page() -> PageState:
    return PageState(
        url=URL,
        title="Contact us",
        fields=(
            BrowserField("name", "Your name", "text", ""),
            BrowserField("email", "Email", "email", ""),
            BrowserField("message", "Message", "textarea", ""),
        ),
    )


def make_graph(tmp_path, scripted, fake):
    settings = Settings(
        loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox"
    )
    return build_graph(ScriptedChatModel(messages=iter(scripted)), settings, transports={"browser": fake})


def test_open_and_fill_run_without_the_gate(tmp_path):
    fake = FakeBrowserTransport({URL: form_page()})
    graph = make_graph(
        tmp_path,
        [
            tool_call_msg("browser_open", {"url": URL}),
            tool_call_msg("browser_fill", {"fields": {"name": "Ada Lovelace", "email": "ada@example.com"}}),
            AIMessage(content="page filled"),
        ],
        fake,
    )
    config = {"configurable": {"thread_id": "t1"}}

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "fill the contact form"}], "run_id": "r1"}, config
    )

    assert "__interrupt__" not in result
    assert fake.submitted == [], "open and fill must never reach submit"
    fills = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert "Ada Lovelace" in fills[-1].content
    assert not (tmp_path / "audit").exists(), "read-only browser tools write no audit lines"


def test_submit_interrupt_shows_the_real_page_state_then_approval_submits_once(tmp_path):
    fake = FakeBrowserTransport({URL: form_page()})
    graph = make_graph(
        tmp_path,
        [
            tool_call_msg("browser_open", {"url": URL}),
            tool_call_msg("browser_fill", {"fields": {"name": "Ada Lovelace", "message": "Hello"}}),
            tool_call_msg("browser_submit", {}),
            AIMessage(content="form submitted"),
        ],
        fake,
    )
    config = {"configurable": {"thread_id": "t2"}}

    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "send the contact form"}], "run_id": "r2"}, config
    )
    payload = paused["__interrupt__"][0].value

    assert fake.submitted == [], "the gate must stop before the transport"
    entry = payload["tools"][0]
    assert entry["name"] == "browser_submit"
    shown = json.loads(entry["args"])
    assert shown["url"] == URL, "the approval carries the transport's real page url"
    assert shown["fields"]["name"] == "Ada Lovelace", "filled values appear even though the tool args are empty"
    assert shown["fields"]["message"] == "Hello"

    result = graph.invoke(Command(resume={"decision": "approve"}), config)

    assert len(fake.submitted) == 1
    decisions = [(record["actor"], record["decision"]) for record in read_audit(tmp_path)]
    assert ("human", "approved") in decisions
    assert ("loom", "executed") in decisions
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert "accepted" in tool_messages[-1].content


def test_submit_reject_leaves_the_transport_untouched(tmp_path):
    fake = FakeBrowserTransport({URL: form_page()})
    graph = make_graph(
        tmp_path,
        [
            tool_call_msg("browser_open", {"url": URL}),
            tool_call_msg("browser_fill", {"fields": {"name": "Ada Lovelace"}}),
            tool_call_msg("browser_submit", {}),
        ],
        fake,
    )
    config = {"configurable": {"thread_id": "t3"}}

    graph.invoke({"messages": [{"role": "user", "content": "send the contact form"}], "run_id": "r3"}, config)
    result = graph.invoke(Command(resume={"decision": "reject"}), config)

    assert fake.submitted == [], "a rejected submit never reaches the transport"
    assert "rejected" in result["messages"][-1].content
    assert [(r["actor"], r["decision"]) for r in read_audit(tmp_path)] == [("human", "rejected")]


def test_oversize_form_state_is_refused_before_approval(tmp_path):
    fake = FakeBrowserTransport({URL: form_page()})
    graph = make_graph(
        tmp_path,
        [
            tool_call_msg("browser_open", {"url": URL}),
            tool_call_msg("browser_fill", {"fields": {"message": "y" * (_APPROVAL_SIZE_LIMIT + 1)}}),
            tool_call_msg("browser_submit", {}),
        ],
        fake,
    )
    config = {"configurable": {"thread_id": "t4"}}

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "send a huge form"}], "run_id": "r4"}, config
    )

    assert "__interrupt__" not in result, "an oversized form state never reaches approval"
    assert fake.submitted == []
    final = result["messages"][-1].content
    assert "Refused before approval" in final and "form state exceeds" in final and "limit" in final
    records = read_audit(tmp_path)
    assert [(record["actor"], record["decision"]) for record in records] == [("loom", "rejected")]
    assert json.loads(records[0]["args"]) == {"reason": "form state exceeds the approval size limit"}


def test_interrupt_shows_the_transport_state_not_the_model_args(tmp_path):
    fake = FakeBrowserTransport({URL: form_page()})
    graph = make_graph(
        tmp_path,
        [
            tool_call_msg("browser_open", {"url": URL}),
            tool_call_msg("browser_fill", {"fields": {"name": "Ada Lovelace", "message": "Hello"}}),
            tool_call_msg("browser_submit", {"url": "http://evil.example", "fields": {"name": "FAKE"}}),
            AIMessage(content="form submitted"),
        ],
        fake,
    )
    config = {"configurable": {"thread_id": "t5"}}

    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "send the contact form"}], "run_id": "r5"}, config
    )
    payload = paused["__interrupt__"][0].value

    shown = json.loads(payload["tools"][0]["args"])
    assert shown["url"] == URL, "the approval carries the transport's real page url"
    assert shown["fields"]["name"] == "Ada Lovelace" and shown["fields"]["message"] == "Hello"
    dumped = json.dumps(payload)
    assert "FAKE" not in dumped and "evil.example" not in dumped, "forged args never reach the approval"

    graph.invoke(Command(resume={"decision": "approve"}), config)
    records = read_audit(tmp_path)
    assert [(record["actor"], record["decision"]) for record in records] == [("human", "approved"), ("loom", "executed")]
    real = {"url": URL, "fields": {"name": "Ada Lovelace", "email": "", "message": "Hello"}}
    for record in records:
        assert json.loads(record["args"]) == real, "every audit row records the transport's state, not the model args"
