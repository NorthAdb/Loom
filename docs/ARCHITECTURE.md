# Architecture

Target shape for Loom. Gates 1–7 are implemented: the core loop, the file sandbox, scheduled routines, the memory baseline, read-only calendar, draft-only mail, browser fill, the Phase 7 tooling (budget, backup, eval), the Gate 6 package model with the task state machine and the Context OS, and the Gate 7 boundary — Request Scope, the Tool Gateway, the untrusted inbound seam, local drafts, and the Operator Console. Checklist: [STATUS.md](STATUS.md). Plan: [ROADMAP.md](ROADMAP.md).

## Implemented loop

The core loop lives in `src/loom`. `loom chat` reads a line and invokes a LangGraph graph whose state is checkpointed by thread id in a `SqliteSaver`. Each turn the `pre_agent` node builds a memory note from the facts table and the semantic store and the `agent` node prepends it as a system message. The `agent` node binds the tool registry: with no tool calls it answers, with harmless-only calls it routes to `tools`, and with any tool flagged `side_effect` it routes to `gate`. The `gate` node calls `interrupt()` with the proposed tool names and redacted arguments (capped at 2000 characters so a mail draft's full recipient, subject, and body are visible for approval), which pauses the graph until the CLI resumes it with approve or reject. Approve routes to `tools`, which executes the calls and appends audit records (each string argument value is capped at 200 characters); reject routes to `aborted`, which answers with a rejection and executes nothing. A turn with no tool calls ends at `post_turn`, which writes the user message back to semantic memory only on explicit preference/fact language and refuses secret-bearing messages. Decisions and executions append JSON lines to `<data dir>/audit/YYYYMMDD.jsonl`; checkpoints live under `<data dir>/checkpoints/threads.sqlite`.

## Layers

1. **Chat and scheduler.** A person talks to Loom. Scheduled routines start work without a chat turn. Both enter the same orchestration path and the same approval rule.
2. **LangGraph.** Primary orchestration. The graph chooses tools, decides when to stop, and decides when to ask for approval. This is the center of the system.
3. **Tools and MCP (calendar, mail, and browser implemented).** Capabilities are tools. The calendar read tools (`calendar_list_events`, `calendar_summarize_range` over an ICS feed), the mail draft tools (`mail_create_draft`, `mail_update_draft` over IMAP), and the browser fill tools (`browser_open`, `browser_fill`, `browser_submit` over headless Playwright chromium) are native: each transport is built from Settings, credentials never enter tool arguments, and unconfigured tools stay in the registry but raise a readable error. The browser transport enforces the host allowlist (loopback plus `LOOM_BROWSER_ALLOWED_HOSTS`) inside the transport, and `browser_submit`'s approval payload is the page's real state read back from the browser via `ToolSpec.approval_context`. MCP remains the intended way to attach further external capabilities. No MCP servers are configured in this repository. Gate 9 is the registry plan (schema fingerprint, per-server credentials, untrusted return values). It is not built.
4. **Memory (implemented baseline).** Two stores with one priority rule. The self-owned facts table (`FactsStore` over `<data dir>/facts.db`) holds authoritative facts and preferences, managed through `loom memory facts`; upsert refuses secret-bearing keys and values. Semantic recall is self-hosted Mem0 (`Mem0SemanticMemory`: openai-compatible chat and embedding models, a local qdrant vector store and history db under `<data dir>/memory/`) behind the `SemanticMemory` protocol, with a no-op implementation when `LOOM_MEMORY_ENABLED` is false; endpoint failures degrade to a facts-only note instead of crashing the turn. Each turn `pre_agent` renders the note (`build_memory_note`): facts first labeled 权威, Mem0 results after labeled 参考, capped at `LOOM_MEMORY_INJECT_LIMIT`, with an explicit instruction to prefer the facts table on conflict. `post_turn` writes a chat message back to semantic memory only when it uses explicit preference/fact language, and refuses anything that looks like a secret. That baseline is what the tree contains, and the Gate 6 Context OS sits on top of it: an episodic log that records every chat turn, a digest `compact` folds it into (one transaction, never touching the facts table), owner feedback via the CLI, and the task state machine (`loom tasks`) with its idempotent effect ledger. Injected context is capped by `LOOM_CONTEXT_INJECT_LIMIT` and is always labeled reference; facts stay authoritative. See ADR-013 in [DECISIONS.md](DECISIONS.md).
5. **Docker sandbox.** Single-host Docker is the runtime. Local file work happens inside the sandbox, which exists now: a jailed dispatcher in `src/loom/tools/sandbox.py` reached either in-process or over HTTP from the `loom-sandbox` compose service (non-root, read-only root filesystem, only the workspace volume mounted). The jail rejects every path that resolves outside the sandbox root, so both transports enforce the same boundary.

## Approval gate

Side effects stop at a human approval gate. The gate sits on the path from orchestration to any tool that changes the world: writes outside the sandbox, browser actions that submit or mutate remote state, and anything similar.

Scheduled routines use the same gate. A schedule is not permission.

Email has no send tool. Calendar tools are read-only. Rules: [SECURITY.md](SECURITY.md).

The gate above is what runs today, and Gate 7 sits in front of it: `open_graph` builds every graph with the Tool Gateway, so a run that carries a `scope_id` (an inbound triage, resumed through the CLI or the console) is checked against its Request Scope at the gate and again at execution, while the operator's own unscoped chat and routines keep the Gate 1–5 human gate. The human gate on a side effect still cannot be skipped.

## Shape today

Built through Gate 5. The Gate 6+ target is the next section, and it is not this diagram.

```text
person / scheduler
        │
        ▼
   LangGraph  ──►  memory (built)
        │           ├── facts / preferences (owned table, authoritative)
        │           └── Mem0 (optional semantic; off when disabled)
        ▼
 approval gate
        │
        ▼
  tools / MCP  ──►  local Docker sandbox (files)
                    calendar read, mail drafts, browser fill (built)
        │
        └── Pi exec / sub-agent: not in this tree (ADR-003)
```

## Target loop (not in this tree)

Design only, locked 2026-11-04 ([ADR-012](DECISIONS.md) through [ADR-016](DECISIONS.md)). Do not read this diagram as code.

```text
untrusted inbound / Operator Console
        │
        ▼
 Life Operator ── Context OS (Gate 6: episodic/digest/feedback/compact, task state)
        │         inbound → Request Scope → Tool Gateway (Gate 7)
        ▼
 approval gate → JSONL audit (correlation_id) + eval
        │
        ▼
 world I/O: local draft is the Gate 8 default; no send
            mailbox IMAP draft stays a separate scope (Gate 3, kept)
            calendar read-only; browser submit still human-gated
```

The console is FastAPI, Jinja2, and HTMX, with Inbox, Draft, Approval, and Audit. It is the skin of the same gate. Specialist workers (Gate 11) are omitted here on purpose.

## Optional Pi sub-agent

Pi may later run as an exec or sub-agent invoked by LangGraph. LangGraph stays the primary orchestrator. Pi is absent until a roadmap item adds it. There is no Pi integration in this tree. Gate 6–11 do not add it. Gate 11’s specialist workers, if they are ever built, are not a Pi integration.

## Outside this repository

Coil is an architecture reference from the 2026-10-30 lock. Loom does not import, fork, or vendor Coil. Nanobot is not the shell.

## What the code layout is today

```text
src/loom/          The package model. The root holds only __init__.py.
  core/            Config, logging, LLM factory, LangGraph loop, runner, runtime
  memory/          Facts store, Mem0 adapter, note builder, and the Gate 6
                   Context OS: episodic log, digest, feedback, compact
  tasks/           Task state machine, store, idempotency ledger
  gateway/         Request Scope, the Tool Gateway, and the pending queue
  tools/           Tool registry (17 tools), drafts, sandbox dispatcher and
                   transports/server, mail, calendar, browser
  inbound/         The untrusted inbound seam and the simulated mail channel
  console/         The local Operator Console (FastAPI app and Jinja2 templates)
  audit/           Append-only JSONL audit log
  ops/             Budget, backup/restore, eval, routines, checkpoints
fixtures/          Sandbox demo input and the simulated inbound fixtures
                   (fixtures/sandbox_messy/, fixtures/inbound/)
scripts/           Gate demos (scripts/demo_gate1.py through demo_gate7_inbound.py)
docs/              Product and engineering record
pyproject.toml     Package metadata and runtime dependencies
Dockerfile         Sandbox service image (two stdlib-only files, non-root)
compose.yml        loom-sandbox service with limits and a healthcheck
```

Add a layer’s code only when [ROADMAP.md](ROADMAP.md) and [STATUS.md](STATUS.md) move that item forward. Do not add unused frameworks ahead of that.
