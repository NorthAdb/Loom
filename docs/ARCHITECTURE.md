# Architecture

Target shape for Loom. None of these layers are implemented. What exists today is the documentation set and a Python package skeleton. Checklist: [STATUS.md](STATUS.md).

## Layers

1. **Chat and scheduler.** A person talks to Loom. Scheduled routines start work without a chat turn. Both enter the same orchestration path and the same approval rule.
2. **LangGraph.** Primary orchestration. The graph chooses tools, decides when to stop, and decides when to ask for approval. This is the center of the system.
3. **Tools and MCP.** Capabilities are tools. MCP is the intended way to attach external capabilities (calendar read, mail draft, browser). No servers are configured in this repository.
4. **Memory.** The first memory cut has two stores: Mem0, and a self-owned table of facts and preferences. Agentic memory is a later layer on top of those stores. It does not replace them.
5. **Docker sandbox.** Single-host Docker is the runtime. Local file work happens inside the sandbox. The sandbox is the process and filesystem boundary for the agent.

## Approval gate

Side effects stop at a human approval gate. The gate sits on the path from orchestration to any tool that changes the world: writes outside the sandbox, browser actions that submit or mutate remote state, and anything similar.

Scheduled routines use the same gate. A schedule is not permission.

Email has no send tool. Calendar tools are read-only. Rules: [SECURITY.md](SECURITY.md).

## Optional Pi sub-agent

Pi may later run as an exec or sub-agent invoked by LangGraph. LangGraph stays the primary orchestrator. Pi is absent until a roadmap item adds it. There is no Pi integration in this tree.

## Outside this repository

Coil is an architecture reference from the 2026-10-30 lock. Loom does not import, fork, or vendor Coil. Nanobot is not the shell.

```text
person / scheduler
        │
        ▼
   LangGraph  ──►  memory
        │           ├── Mem0
        │           └── facts / preferences (owned table)
        │           └── agentic memory (later)
        ▼
 approval gate
        │
        ▼
  tools / MCP  ──►  local Docker sandbox (files, routine host)
        │
        └── optional later: Pi exec / sub-agent, called by LangGraph
```

## What the code layout is today

```text
src/loom/          Python package skeleton (__version__ only)
docs/              Product and engineering record
pyproject.toml     Package metadata, no runtime dependencies
```

Add a layer’s code only when [ROADMAP.md](ROADMAP.md) and [STATUS.md](STATUS.md) move that item forward. Do not add unused frameworks ahead of that.
