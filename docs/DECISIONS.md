# Decisions

Choices locked on 2026-10-30, with later ADRs dated in place. The repository is the source of truth for what is done and for what was decided. This file records intent. [STATUS.md](STATUS.md) records implementation. An external research page does not replace either file (ADR-017).

When a choice changes, add a new ADR with the date and update the docs that would otherwise be wrong: [VISION.md](VISION.md), [ARCHITECTURE.md](ARCHITECTURE.md), [ROADMAP.md](ROADMAP.md), [SECURITY.md](SECURITY.md), and the README.

## ADR-001 — Name and positioning

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** The project is named Loom. It is a deep-and-wide personal agent. Self-use reliability comes first. More daily scenarios come after the owner can use it every day.
- **Consequences:** The first user is the owner. Sequencing favors a trustworthy daily loop over a wide surface.

## ADR-002 — Local single-host Docker

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** Execution is local Docker on one host.
- **Consequences:** Deployment, files, and secrets are designed for a single machine the owner controls. A hosted multi-tenant runtime is out of scope.

## ADR-003 — LangGraph primary, Pi optional later

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** LangGraph is the primary orchestrator. Pi may later act as an exec or sub-agent under LangGraph.
- **Consequences:** Graphs, checkpoints, and tool routing are LangGraph’s job. Pi is not a second top-level shell and is not part of the bootstrap.

## ADR-004 — Memory baseline, then agentic memory

- **Status:** Accepted. The baseline stands. The clause “agentic memory comes after that baseline” is superseded by ADR-013 (2026-11-04).
- **Date:** 2026-10-30
- **Decision:** The memory baseline is Mem0 plus a self-owned facts and preferences table. Agentic memory comes after that baseline exists.
- **Consequences:** Facts the owner cares about have a store Loom owns, separate from Mem0. Agentic memory is not a shortcut around that table. ADR-013 keeps the table and the optional Mem0 layer, and replaces “agentic memory” as the next step.

## ADR-005 — Python

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** The implementation language is Python.
- **Consequences:** The package name is `loom` under `src/loom/`. Runtime dependencies are added only when a roadmap item needs them.

## ADR-006 — Coil is inspiration only

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** Cloudflare Coil may inform the architecture. Loom does not fork Coil, vendor Coil, or use Coil as the runtime.
- **Consequences:** No Coil source tree, submodule, or Coil-specific runtime config belongs in this repository.

## ADR-007 — Nanobot is not the shell

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** Nanobot is not the main shell.
- **Consequences:** Entry points, scheduling, and chat go through Loom’s own orchestration path.

## ADR-008 — Scenario boundaries

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** Target scenarios and their limits are:
  - Calendar: read-only summary
  - Email: draft only, never send
  - Browser: fill, then human confirm before a side effect
  - Files: local sandbox
  - Time: scheduled routines
- **Consequences:** Tools that send mail, write calendar events, or submit in a browser without a confirm are out of scope. See [SECURITY.md](SECURITY.md).

## ADR-009 — Build order

- **Status:** Accepted. The sequence is complete at Gate 5 (main tip `1711d1b`, 2026-11-04). Later sequencing is ADR-012.
- **Date:** 2026-10-30
- **Decision:** Build order is file sandbox, scheduled routines, and an approval gate, then calendar and email drafts, then a deeper browser-fill path.
- **Consequences:** That order is what Gate 1–5 shipped. [ROADMAP.md](ROADMAP.md) now follows ADR-012. External-account work did not precede the sandbox, routines, and the gate.

## ADR-010 — Repository state tracks progress

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** The repository is the source of truth for what is done and what is not. Docs, especially [STATUS.md](STATUS.md), move in the same change as the work they describe.
- **Consequences:** A feature is not done because a chat or a roadmap bullet says so. [CONTRIBUTING.md](../CONTRIBUTING.md) requires the status update. ADR-017 extends the same rule to locked decisions: an external page is not the record.

## ADR-011 — Browser fill is native, allowlisted, and gated at submit

- **Status:** Accepted (reworked after the Gate 4 review, 2026-11-01)
- **Date:** 2026-11-01
- **Decision:** Browser support is a native transport (headless Playwright chromium behind the `browser` extra), the same Settings-built seam as calendar and mail, not an MCP server. `browser_open` and `browser_fill` stay unapproved because they submit nothing; `browser_submit` is the module's single click and its only gated verb. A request firewall passes every request the page makes through the host allowlist — loopback is always allowed, every other host must be listed in `LOOM_BROWSER_ALLOWED_HOSTS` — and aborts the rest before it is sent; the url a click will use (the control's `formaction`, else its owning form's action) is also checked before the click, and a submission the firewall blocks raises instead of reading as accepted. The submit approval shows the page's real url and field values read back from the transport (`ToolSpec.approval_context`), merged over anything the model claims, and password fields never leave the browser: the model and the approval see `***`.
- **Consequences:** Redirect responses are followed by chromium below the route interception layer, so a navigation that lands off the allowlist closes the page and raises; a 307/308 redirect could re-send a request body before that close, a known limit of the interception layer. This gate renders url + field dictionaries instead of screenshots; screenshots are a later layer. Filling a page can trigger page-owned traffic (autosave) on allowlisted remote hosts, so keep allowlists narrow. MCP remains the path for capabilities beyond this surface. The Gate 9 registry in [ROADMAP.md](ROADMAP.md) is that plan; it is not built.

## ADR-012 — Life Operator

- **Status:** Accepted
- **Date:** 2026-11-04
- **Decision:** The next product bet is one Life Operator. The loop is untrusted inbound, then a Request Scope, then a Tool Gateway check outside the model, then a gated life action, then an append-only JSONL audit keyed by `correlation_id`, plus eval. Mail triage is the flagship example, not the only channel. One operator faces the world. Specialist workers are Gate 11 and stay deferred. A peer swarm is out of scope.
- **Consequences:** Gate 1–5 stay. They are not replaced. [ROADMAP.md](ROADMAP.md) orders the next work: Gate 6–8 are P0, Gate 9 is P1, Gate 10 and Gate 11 are P2. ADR-009 finished at Gate 5. The hard boundaries in ADR-008 do not move: no send, calendar read-only, browser submit stays human-approved, secrets stay out.

## ADR-013 — Light memory and Task State

- **Status:** Accepted
- **Date:** 2026-11-04
- **Supersedes:** the “agentic memory comes after the baseline” clause of ADR-004. The facts table and the optional Mem0 baseline remain.
- **Decision:** Memory stays light. Gate 6 is a Context OS of Facts, a digest, an episodic log, feedback, compact, and Task State. Task State is a persistent state machine: a restart restores it, and external side effects are idempotent. There is no thick RAG spine. Mem0 stays the optional semantic layer behind the existing flag. It is not part of Gate 6, and it is not the authority. Facts stay authoritative. Inbound content does not silently write facts.
- **Consequences:** “Agentic memory” is not a roadmap item. Conflict handling is supersede, provenance, and an explicit priority (facts win), not recall@k. A vector index, if one is added later, cannot promote a hit into a fact.

## ADR-014 — Request Scope and the Tool Gateway

- **Status:** Accepted
- **Date:** 2026-11-04
- **Decision:** Request Scope is a real object, checked by a Tool Gateway outside the model. The model may propose an action. It cannot grant a scope. Identity is not the input source: an email From header, or a body that claims to be the owner, is not an authenticated principal. External content (mail, HTML, attachments, tool return values) is untrusted and is not an instruction. Reading content, following an instruction, writing a fact, and changing a permission are separate. The fields are `scope_id`, `principal`, `channel`, `resource`, `allowed_actions`, `memory_write`, `approval_required`, `expires_at`, and `correlation_id`. `send_permission` is always false. If authorizing a side effect or writing its audit record fails, the action fails closed.
- **Consequences:** Gate 7 introduces the object, the gateway, and `correlation_id` on new audit rows. Until that code exists, the Phase 1 approval gate remains the only side-effect check, no scope is issued, and today’s audit lines do not carry `correlation_id`. The audit stays append-only JSONL. “Traceable” is the claim. Tamper-proof is not. A hash chain is deferred. See [SECURITY.md](SECURITY.md).

## ADR-015 — Operator Console

- **Status:** Accepted
- **Date:** 2026-11-04
- **Decision:** The UI is a local Operator Console: FastAPI, Jinja2, and HTMX, in this Python repository. Four views: Inbox, Draft, Approval, and Audit. It binds to the local machine. It checks CSRF and Origin/Host, and an approval requires a session, not a hidden button. Gate 7 is the shell and may use simulated inbound. Gate 8 embeds real mail triage. The console is the skin of the same gate, not a second product. Gradio, Streamlit, Next, Electron, and Tauri are not the choice. A separate React or Vite chat surface is out of this sequence.
- **Consequences:** No public hosting (ADR-002). The Draft view has no send control (ADR-016). Every life action on the page still goes through the scope, the gateway, the approval gate, and the JSONL audit.

## ADR-016 — Email side effects are layered

- **Status:** Accepted
- **Date:** 2026-11-04
- **Decision:** “No send” does not mean “no side effects.” Mail side effects are layered, each with its own scope:
  1. Read.
  2. Local classify and a local draft. This is the Gate 8 default.
  3. Mailbox label or read-state changes. Optional, and not the default.
  4. Mailbox draft (IMAP APPEND). The Gate 3 tools, kept as a separate Scope plus approval. Not bundled into the default triage, and not removed.
  5. Send. No tool is registered.
- **Consequences:** ADR-008’s “draft only, never send” still holds. Gate 8’s flagship path writes a local draft, not the mailbox. `mail_create_draft` and `mail_update_draft` stay behind their own approval. The Draft view has no send control. A routine must not add a send tool.

## ADR-017 — Repository docs, not an external page

- **Status:** Accepted
- **Date:** 2026-11-04
- **Decision:** Progress and locked decisions live in this repository. [STATUS.md](STATUS.md) is what the tree contains. [ROADMAP.md](ROADMAP.md) is the plan. This file is intent. An external research page, canvas, or chat may inform a change. It does not replace these files, and it is not evidence that a feature shipped.
- **Consequences:** ADR-010 still requires STATUS.md to move in the same change as the work. A decision change adds an ADR here and updates the docs that would otherwise be wrong. [CONTRIBUTING.md](../CONTRIBUTING.md) says the same. The 2026-11-04 research canvas informed ADR-012 through ADR-016 (https://cursor.com/canvas/store-13fa9a48-da91-4ebe-91b1-dfcb142a1f42/cf35f0d0-56da-4598-a24c-5f6b37114b0c). If that page and this repository disagree, this repository wins.

## ADR-018 — The package kernel and the layout guard

- **Status:** Accepted
- **Date:** 2026-11-05
- **Decision:** The §2.3 dependency direction governs who orchestrates whom. `loom.core.config` and `loom.core.logging` are kernel leaves every package may import (they import nothing from loom), so `memory/` or `tasks/` reading `Settings` or `is_sensitive` is not reverse flow. The disclosed sibling arrows beyond the handoff's table are: `memory/facts` and `gateway/scope` import `loom.ops.routines` (now_local, write_json_atomic), `tasks/store` imports `loom.memory.facts` (the shared secret guard), and `console/app` imports `loom.tools.drafts` (the console reads the store the draft tool writes). The root of `src/loom` holds only `__init__.py`; all compatibility shims from the Gate 6 transition are deleted and none may return. `tests/test_package_layout.py` pins the root, the critical bans (tools never imports console or cli; inbound touches only tasks, gateway.scope, the audit, and the inbox store; the console never touches the mint or the graph build; the console binds 127.0.0.1), and the no-send symbol scan runs recursively over all of `src/loom`.
- **Consequences:** A new subpackage does not get a free pass past the guard: the layout test grows with the tree. If a kernel helper grows business meaning, it moves out of the kernel and the importers follow.

## ADR-019 — Backup roots and scope retention

- **Status:** Accepted
- **Date:** 2026-11-05
- **Decision:** `BACKUP_ROOTS` is nine: facts.db, routines.json, approvals, audit, checkpoints, tasks.db, context.db, drafts.json, and inbound. The task machine, the Context OS, the local drafts, and the inbox are durable operator state a restore must keep. `scopes.json` is deliberately excluded: scopes are short-lived authorizations, and a restore that silently re-granted them would be the wrong failure mode. After a restore, inbound work needs fresh authorization — refuse rather than assume.
- **Consequences:** A restore revokes every outstanding scope (the fail-safe direction). Backup/restore tests pin the round trip for the task machine, the folded context, the drafts, and the inbox.

