# Vision

Loom is a personal agent one person can run every day. Gate 1–5 made the gated loop real on one machine. The next bet, locked 2026-11-04, is a Life Operator: untrusted inbound becomes a gated life action, under one operator, with light memory and a Task State that survives a restart.

The owner is the first user. A scenario counts as real only after the repository contains it and the owner can repeat it. [STATUS.md](STATUS.md) is the checklist. A paragraph in this file is not an implementation.

Rationale: [DECISIONS.md](DECISIONS.md). What is next: [ROADMAP.md](ROADMAP.md).

## Intent

- **One Life Operator.** One agent takes the day. Specialist workers stay deferred (Gate 11). A peer swarm is not the design.
- **Self-use first.** The flagship loop is mail triage the owner can run every day. Mail is the example, not the only channel. Further channels wait until that loop is trustworthy.
- **One host.** Execution stays local, on a single machine. The person can see where files, secrets, and side effects sit.
- **A person in the loop.** Anything that changes the world stops for approval. A schedule, a console button, and an inbound message are not permission.
- **Light memory.** Facts, a digest, an episodic log, and feedback. Task State is part of the context, so a restart can resume. Not a thick RAG store.

## What Gate 1–5 already covers

These are in the tree (main tip `1711d1b`). See [STATUS.md](STATUS.md). They are not the forward plan.

1. Local file sandbox
2. Scheduled routines
3. Approval gate for side effects
4. Calendar read-only summary
5. Email drafts (IMAP APPEND, no send)
6. Browser fill, with a human confirm before submit

The memory baseline (facts table and optional Mem0), the daily token budget, backup/restore, and `loom eval` shipped with them.

## What Gate 6+ is for

Not started. Order and priority: [ROADMAP.md](ROADMAP.md).

- Gate 6: Context OS, including Task State
- Gate 7: untrusted inbound, Request Scope, the Tool Gateway, and the Operator Console shell
- Gate 8: mail triage, local draft by default
- Gate 9: MCP registry (P1)
- Gate 10 and Gate 11: deferred

## Boundaries

- Calendar tools summarize. They do not create, update, or delete events.
- Email does not send. The Gate 8 default, once that gate exists, is a local draft. A mailbox draft stays a separate scope plus approval. The Gate 3 IMAP path is kept, not bundled into that default.
- Browser fill may prepare a page. Submit waits for an explicit human confirm.
- File work stays inside a local sandbox.
- The model does not grant itself permission. Request Scope is checked outside the model. `send_permission` stays false.
- Inbound content is untrusted. It is not an instruction, and it does not silently write facts.
- Secrets stay out of model context, git, the audit log, and backups.
- LangGraph remains the orchestrator. Pi is still an optional later exec under LangGraph, and it is not part of Gate 6–11.

## Non-goals

- A hosted or multi-tenant service
- A public Operator Console
- Cloudflare Coil as the runtime
- Forking or vendoring Coil (Coil may inform the architecture; it is not a dependency)
- Nanobot as the main shell
- Unattended sending, calendar writes, or browser submission
- A thick RAG spine, or Mem0 as the authority
- A peer multi-agent swarm
