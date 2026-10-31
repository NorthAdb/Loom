# Architecture

Target shape for Loom. The core loop, the file sandbox layer, and the memory baseline are implemented; the remaining layers exist only as design. Checklist: [STATUS.md](STATUS.md).

## Implemented loop

The core loop lives in `src/loom`. `loom chat` reads a line and invokes a LangGraph graph whose state is checkpointed by thread id in a `SqliteSaver`. Each turn the `pre_agent` node builds a memory note from the facts table and the semantic store and the `agent` node prepends it as a system message. The `agent` node binds the tool registry: with no tool calls it answers, with harmless-only calls it routes to `tools`, and with any tool flagged `side_effect` it routes to `gate`. The `gate` node calls `interrupt()` with the proposed tool names and redacted arguments, which pauses the graph until the CLI resumes it with approve or reject. Approve routes to `tools`, which executes the calls and appends audit records; reject routes to `aborted`, which answers with a rejection and executes nothing. A turn with no tool calls ends at `post_turn`, which writes the user message back to semantic memory only on explicit preference/fact language and refuses secret-bearing messages. Decisions and executions append JSON lines to `<data dir>/audit/YYYYMMDD.jsonl`; checkpoints live under `<data dir>/checkpoints/threads.sqlite`.

## Layers

1. **Chat and scheduler.** A person talks to Loom. Scheduled routines start work without a chat turn. Both enter the same orchestration path and the same approval rule.
2. **LangGraph.** Primary orchestration. The graph chooses tools, decides when to stop, and decides when to ask for approval. This is the center of the system.
3. **Tools and MCP.** Capabilities are tools. MCP is the intended way to attach external capabilities (calendar read, mail draft, browser). No servers are configured in this repository.
4. **Memory (implemented baseline).** Two stores with one priority rule. The self-owned facts table (`FactsStore` over `<data dir>/facts.db`) holds authoritative facts and preferences, managed through `loom memory facts`; upsert refuses secret-bearing keys and values. Semantic recall is self-hosted Mem0 (`Mem0SemanticMemory`: openai-compatible chat and embedding models, a local qdrant vector store and history db under `<data dir>/memory/`) behind the `SemanticMemory` protocol, with a no-op implementation when `LOOM_MEMORY_ENABLED` is false; endpoint failures degrade to a facts-only note instead of crashing the turn. Each turn `pre_agent` renders the note (`build_memory_note`): facts first labeled 权威, Mem0 results after labeled 参考, capped at `LOOM_MEMORY_INJECT_LIMIT`, with an explicit instruction to prefer the facts table on conflict. `post_turn` writes a chat message back to semantic memory only when it uses explicit preference/fact language, and refuses anything that looks like a secret. Agentic memory is a later layer on top of these stores. It does not replace them.
5. **Docker sandbox.** Single-host Docker is the runtime. Local file work happens inside the sandbox, which exists now: a jailed dispatcher in `src/loom/sandbox.py` reached either in-process or over HTTP from the `loom-sandbox` compose service (non-root, read-only root filesystem, only the workspace volume mounted). The jail rejects every path that resolves outside the sandbox root, so both transports enforce the same boundary.

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
src/loom/          Config, logging, tool registry, sandbox dispatcher and transports,
                   sandbox HTTP server, audit log, memory (facts store, Mem0 adapter,
                   note builder), LangGraph loop, CLI
fixtures/          Sandbox demo input (fixtures/sandbox_messy/)
scripts/           Gate demos (scripts/demo_gate1.py, scripts/demo_gate2.py,
                   scripts/demo_gate3_memory.py)
docs/              Product and engineering record
pyproject.toml     Package metadata and runtime dependencies
Dockerfile         Sandbox service image (two stdlib-only files, non-root)
compose.yml        loom-sandbox service with limits and a healthcheck
```

Add a layer’s code only when [ROADMAP.md](ROADMAP.md) and [STATUS.md](STATUS.md) move that item forward. Do not add unused frameworks ahead of that.
