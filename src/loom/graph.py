"""The LangGraph loop: agent -> gate for side effects -> tools, or aborted on rejection."""

from typing import Annotated, Literal, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from loom.audit import AuditLog, summarize_args
from loom.checkpoints import make_checkpointer
from loom.config import Settings
from loom.tools import make_tools


class GraphState(TypedDict):
    messages: Annotated[list, add_messages]
    pending_action: dict | None
    approval_status: Literal["approved", "rejected"] | None
    run_id: str
    metadata: dict


def build_graph(model: BaseChatModel, settings: Settings, checkpointer=None):
    """Compile the agent/gate/tools/aborted graph. Long-lived callers pass the
    checkpointer so the connection lifecycle stays with them."""
    registry = make_tools(settings.loom_data_dir)
    audit = AuditLog(settings.loom_data_dir)
    bound = model.bind_tools([spec.func for spec in registry.values()])

    def agent(state: GraphState) -> dict:
        return {"messages": [bound.invoke(state["messages"])]}

    def _last_tool_calls(state: GraphState) -> list[dict]:
        last = state["messages"][-1]
        return list(getattr(last, "tool_calls", None) or [])

    def route_after_agent(state: GraphState) -> str:
        calls = _last_tool_calls(state)
        if not calls:
            return END
        if any(spec.side_effect for call in calls if (spec := registry.get(call["name"]))):
            return "gate"
        return "tools"

    def gate(state: GraphState) -> dict:
        calls = [c for c in _last_tool_calls(state) if (spec := registry.get(c["name"])) and spec.side_effect]
        payload = {
            "tools": [{"name": c["name"], "args": summarize_args(c["args"])} for c in calls],
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

    def aborted(state: GraphState) -> dict:
        names = ", ".join(item["name"] for item in (state.get("pending_action") or {}).get("tools", []))
        text = f"The proposed action ({names}) was rejected and was not executed."
        return {"messages": [AIMessage(content=text)], "pending_action": None}

    graph = StateGraph(GraphState)
    graph.add_node("agent", agent)
    graph.add_node("gate", gate)
    graph.add_node("tools", run_tools)
    graph.add_node("aborted", aborted)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["gate", "tools", END])
    graph.add_conditional_edges("gate", route_after_gate, ["tools", "aborted"])
    graph.add_edge("tools", "agent")
    graph.add_edge("aborted", END)
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else make_checkpointer(settings.loom_data_dir))
