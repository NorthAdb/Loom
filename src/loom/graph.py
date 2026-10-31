"""The LangGraph loop: memory note -> agent -> gate for side effects -> tools, write-back, or aborted on rejection."""

import logging
import sqlite3
from typing import Annotated, Literal, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from loom.audit import AuditLog, summarize_args
from loom.checkpoints import make_checkpointer
from loom.config import Settings
from loom.memory import (
    FactsStore,
    SemanticMemory,
    build_memory_note,
    build_semantic_memory,
    contains_secret,
    has_explicit_memory_intent,
)
from loom.tools import make_tools

logger = logging.getLogger("loom")

MEMORY_REFUSAL = (
    "Write refused: that message looks like it contains an API key or token, "
    "so nothing was saved to memory."
)

_GATE_PAYLOAD_CAP = 2000

_MAIL_DRAFT_TOOLS = {"mail_create_draft", "mail_update_draft"}
_DRAFT_SIZE_LIMIT = 10_000


def _draft_args_size(args: dict) -> int:
    """Combined length of a draft's to+subject+body, the gate's refusal basis."""
    return sum(len(str(args.get(name, "") or "")) for name in ("to", "subject", "body"))


class GraphState(TypedDict):
    messages: Annotated[list, add_messages]
    pending_action: dict | None
    approval_status: Literal["approved", "rejected"] | None
    memory_note: str
    run_id: str
    metadata: dict


def _last_human_text(state: GraphState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def build_graph(
    model: BaseChatModel,
    settings: Settings,
    checkpointer=None,
    memory: SemanticMemory | None = None,
    transports: dict | None = None,
):
    """Compile the pre_agent/agent/gate/tools/post_turn/aborted graph. Long-lived callers pass the
    checkpointer so the connection lifecycle stays with them. Tests inject a fake `memory` and
    fake mail/calendar `transports`; None builds the real ones from settings."""
    registry = make_tools(settings, transports)
    audit = AuditLog(settings.loom_data_dir)
    facts_store = FactsStore(settings.loom_data_dir)
    semantic: SemanticMemory = memory if memory is not None else build_semantic_memory(settings)
    bound = model.bind_tools([spec.func for spec in registry.values()])

    def pre_agent(state: GraphState) -> dict:
        try:
            facts = facts_store.list()
        except (OSError, sqlite3.Error) as exc:
            logger.warning("facts store unavailable; injecting an empty note", extra={"error": str(exc)})
            facts = []
        memories: list[str] = []
        user_text = _last_human_text(state)
        if user_text:
            try:
                memories = semantic.search(user_text, limit=settings.memory_inject_limit)
            except (RuntimeError, OSError) as exc:
                logger.warning("semantic memory unavailable; injecting facts only", extra={"error": str(exc)})
        return {"memory_note": build_memory_note(facts, memories, limit=settings.memory_inject_limit)}

    def agent(state: GraphState) -> dict:
        messages = state["messages"]
        note = state.get("memory_note") or ""
        if note:
            messages = [SystemMessage(content=note), *messages]
        return {"messages": [bound.invoke(messages)]}

    def _last_tool_calls(state: GraphState) -> list[dict]:
        last = state["messages"][-1]
        return list(getattr(last, "tool_calls", None) or [])

    def route_after_agent(state: GraphState) -> str:
        calls = _last_tool_calls(state)
        if not calls:
            return "post_turn"
        if any(spec.side_effect for call in calls if (spec := registry.get(call["name"]))):
            return "gate"
        return "tools"

    def gate(state: GraphState) -> dict:
        calls = [c for c in _last_tool_calls(state) if (spec := registry.get(c["name"])) and spec.side_effect]
        oversized = [
            c for c in calls if c["name"] in _MAIL_DRAFT_TOOLS and _draft_args_size(c["args"]) > _DRAFT_SIZE_LIMIT
        ]
        if oversized:
            # Refused before approval: no interrupt, nothing executes, and the
            for c in oversized:
                audit.append(
                    run_id=state.get("run_id", ""),
                    actor="loom",
                    tool=c["name"],
                    args={"reason": "draft exceeds the approval size limit"},
                    decision="rejected",
                )
            c = oversized[0]
            payload = {
                "tools": [{"name": c["name"], "args": summarize_args(c["args"])}],
                "refusal": (
                    f"Draft refused before approval: to+subject+body exceed the "
                    f"{_DRAFT_SIZE_LIMIT}-character approval size limit."
                ),
            }
            return {"approval_status": "rejected", "pending_action": payload}
        payload = {
            "tools": [
                {
                    "name": c["name"],
                    "args": summarize_args(
                        c["args"], cap=_DRAFT_SIZE_LIMIT if c["name"] in _MAIL_DRAFT_TOOLS else _GATE_PAYLOAD_CAP
                    ),
                }
                for c in calls
            ],
            "reason": "These tools change state and need human approval.",
        }
        resume = interrupt(payload)
        status = "approved" if resume["decision"] == "approve" else "rejected"
        for c in calls:
            audit.append(
                run_id=state.get("run_id", ""),
                actor="human",
                tool=c["name"],
                args=c["args"],
                decision=status,
            )
        return {"approval_status": status, "pending_action": payload}

    def route_after_gate(state: GraphState) -> str:
        return "tools" if state["approval_status"] == "approved" else "aborted"

    def run_tools(state: GraphState) -> dict:
        messages: list[ToolMessage] = []
        for call in _last_tool_calls(state):
            spec = registry.get(call["name"])
            if spec is None:
                messages.append(ToolMessage(content=f"error: unknown tool {call['name']}", tool_call_id=call["id"]))
                continue
            try:
                result = spec.func.invoke(call["args"])
                messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
            except Exception as exc:  # noqa: BLE001 - a tool failure reaches the model as a result, not a crash
                messages.append(ToolMessage(content=f"error: {exc}", tool_call_id=call["id"]))
                if spec.side_effect:
                    audit.append(
                        run_id=state.get("run_id", ""),
                        actor="loom",
                        tool=call["name"],
                        args=call["args"],
                        decision="failed",
                    )
                continue
            if spec.side_effect:
                audit.append(
                    run_id=state.get("run_id", ""),
                    actor="loom",
                    tool=call["name"],
                    args=call["args"],
                    decision="executed",
                )
        return {"messages": messages, "pending_action": None}

    def post_turn(state: GraphState) -> dict:
        text = _last_human_text(state)
        if not text or not has_explicit_memory_intent(text):
            return {}
        if contains_secret(text):
            logger.warning("refused to memorize message: it looks like it carries a secret")
            return {"messages": [AIMessage(content=MEMORY_REFUSAL)]}
        try:
            semantic.add([text])
        except (RuntimeError, OSError) as exc:
            logger.warning("memory write-back failed; turn continues", extra={"error": str(exc)})
        return {}

    def aborted(state: GraphState) -> dict:
        payload = state.get("pending_action") or {}
        names = ", ".join(item["name"] for item in payload.get("tools", []))
        text = f"The proposed action ({names}) was rejected and was not executed."
        refusal = payload.get("refusal")
        if refusal:
            text = f"{text} {refusal}"
        return {"messages": [AIMessage(content=text)], "pending_action": None}

    graph = StateGraph(GraphState)
    graph.add_node("pre_agent", pre_agent)
    graph.add_node("agent", agent)
    graph.add_node("gate", gate)
    graph.add_node("tools", run_tools)
    graph.add_node("post_turn", post_turn)
    graph.add_node("aborted", aborted)
    graph.add_edge(START, "pre_agent")
    graph.add_edge("pre_agent", "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["gate", "tools", "post_turn"])
    graph.add_conditional_edges("gate", route_after_gate, ["tools", "aborted"])
    graph.add_edge("tools", "agent")
    graph.add_edge("post_turn", END)
    graph.add_edge("aborted", END)
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else make_checkpointer(settings.loom_data_dir))
