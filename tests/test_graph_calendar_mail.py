"""The graph with calendar and mail tools: read-only answers, gate, audit, draft id."""

from conftest import (
    CALENDAR_ICS,
    FakeCalendarTransport,
    FakeMailTransport,
    ScriptedChatModel,
    read_audit,
    tool_call_msg,
)
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from loom.config import Settings
from loom.graph import build_graph

DRAFT_BODY = "Approval shows the whole draft text, not a truncation. " * 6


def make_graph(tmp_path, scripted, transports):
    settings = Settings(
        loom_env="dev",
        loom_data_dir=tmp_path,
        loom_sandbox_root=tmp_path / "sandbox",
        mail_user="owner@example.com",
    )
    model = ScriptedChatModel(messages=iter(scripted))
    return build_graph(model, settings, transports=transports)


def test_calendar_tool_answers_without_gate_or_audit(tmp_path):
    graph = make_graph(
        tmp_path,
        [
            tool_call_msg("calendar_summarize_range", {"start": "2026-11-01", "end": "2026-11-23"}),
            AIMessage(content="here is your week"),
        ],
        {"calendar": FakeCalendarTransport(CALENDAR_ICS)},
    )

    result = graph.invoke(
        {"messages": [{"role": "user", "content": "what's on between those dates?"}], "run_id": "r1"},
        {"configurable": {"thread_id": "t1"}},
    )

    assert "__interrupt__" not in result
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert "Standup" in tool_messages[-1].content and "Team offsite" in tool_messages[-1].content
    assert not (tmp_path / "audit").exists(), "read-only tools write no audit lines"


def test_draft_reject_stops_before_the_transport_and_audits_rejected(tmp_path):
    mail = FakeMailTransport()
    graph = make_graph(
        tmp_path,
        [tool_call_msg("mail_create_draft", {"to": "peer@example.com", "subject": "Plan", "body": DRAFT_BODY})],
        {"mail": mail},
    )
    config = {"configurable": {"thread_id": "t2"}}

    paused = graph.invoke(
        {"messages": [{"role": "user", "content": "draft a mail to peer"}], "run_id": "r2"}, config
    )
    payload = paused["__interrupt__"][0].value

    assert payload["tools"][0]["name"] == "mail_create_draft"
    assert DRAFT_BODY in payload["tools"][0]["args"], "the approval must show the full draft body"
    assert mail.appended == []

    result = graph.invoke(Command(resume={"decision": "reject"}), config)

    assert mail.appended == [], "reject means the IMAP server is never called"
    assert [record["decision"] for record in read_audit(tmp_path)] == ["rejected"]
    assert "rejected" in result["messages"][-1].content


def test_draft_approve_calls_the_transport_once_and_reports_the_draft_id(tmp_path):
    mail = FakeMailTransport()
    graph = make_graph(
        tmp_path,
        [
            tool_call_msg("mail_create_draft", {"to": "peer@example.com", "subject": "Plan", "body": DRAFT_BODY}),
            AIMessage(content="draft saved"),
        ],
        {"mail": mail},
    )
    config = {"configurable": {"thread_id": "t3"}}

    graph.invoke({"messages": [{"role": "user", "content": "draft a mail to peer"}], "run_id": "r3"}, config)
    result = graph.invoke(Command(resume={"decision": "approve"}), config)

    assert len(mail.appended) == 1
    message = mail.appended[0].decode("utf-8")
    assert "peer@example.com" in message and "Plan" in message
    decisions = [(record["actor"], record["decision"]) for record in read_audit(tmp_path)]
    assert ("human", "approved") in decisions
    assert ("loom", "executed") in decisions
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert "fake-draft-1" in tool_messages[-1].content
