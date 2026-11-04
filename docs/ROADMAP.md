# Roadmap

Locked 2026-11-04. Gate 1–5 are done on main tip `1711d1b`. The old P0–P3 order (sandbox, then calendar and mail drafts, then browser fill) finished with them. See [ADR-009](DECISIONS.md) and [ADR-012](DECISIONS.md).

The next build order is:

1. Gate 6 — Context OS, including Task State
2. Gate 7 — untrusted inbound, Request Scope, the Tool Gateway, and the Operator Console shell
3. Gate 8 — mail triage, with a local draft as the default

Gate 9 is P1. Gate 10 and Gate 11 are P2 and stay deferred. Implementation state lives only in [STATUS.md](STATUS.md).

## Product loop

Untrusted inbound → Request Scope (a programmatic permission object) → a Tool Gateway check outside the model → a gated life action → append-only JSONL audit keyed by `correlation_id`, plus eval.

One Life Operator faces the world. Mail triage is the flagship example, not the only channel. Specialist workers wait until Gate 11.

## Hard boundaries

Unchanged. They apply to every gate below. Detail: [SECURITY.md](SECURITY.md), [ADR-008](DECISIONS.md), [ADR-016](DECISIONS.md).

- No send tool is registered. `send_permission` is false. The Draft view has no send control.
- Calendar stays read-only. No create, update, or delete.
- Secrets stay out of the model, git, the audit log, and backups.
- Browser submit still requires a person.
- Inbound content is untrusted. It is not an instruction. An email From header, or a body that claims to be the owner, is not an authenticated principal.
- If authorizing a side effect or writing its audit record fails, the action fails closed.
- Audit P0 is append-only JSONL. Gate 7 adds `correlation_id` on new rows so a draft is traceable to its scope; rows since Gate 7 carry it. Traceable does not mean tamper-proof. A hash chain is deferred.

## Priority

| Priority | Gates | What they are for |
| --- | --- | --- |
| P0 | Gate 6, Gate 7, Gate 8 | The life-operator loop the owner can run |
| P1 | Gate 9 | The same gate on an MCP registry |
| P2 | Gate 10, Gate 11 | Deferred. Not pulled forward to fill a gap in P0 |

## Gate 6 — Context OS (P0)

Light memory, not a thick RAG spine. Mem0 stays optional and is not part of this gate ([ADR-013](DECISIONS.md) supersedes the “agentic memory next” reading of [ADR-004](DECISIONS.md)).

- Facts stay the authoritative table already in the tree
- Add a digest, an episodic log, feedback, and compact of the working set
- Task State: a persistent state machine. A restart restores it. External side effects are idempotent
- Inbound content does not silently write facts

## Gate 7 — Inbound, Request Scope, Console (P0)

The shell. Simulated inbound is enough. Real mail waits for Gate 8.

- Inbound (mail, and the same seam for a later IM or webhook) is untrusted. Reading it, following an instruction, writing a fact, and changing a permission are separate steps
- Request Scope is a real object with the fields in [ADR-014](DECISIONS.md). The model may propose an action. It cannot grant a scope. `send_permission` is always false
- The Tool Gateway checks that object outside the model before a tool runs
- Operator Console: FastAPI, Jinja2, and HTMX, in this repo. Four views: Inbox, Draft, Approval, Audit. Bind locally. CSRF, Origin/Host checks, and a session. Approval depends on the session, not a hidden button ([ADR-015](DECISIONS.md))
- Exit bar: at least ten negative tests on simulated inbound, including a forged principal, a hidden instruction that tries to write facts, and a demand to send

## Gate 8 — Mail triage (P0)

Real mail on the Gate 7 console. The flagship path writes a local draft.

Side effects stay layered ([ADR-016](DECISIONS.md)):

1. Read
2. Local classify and a local draft — the default
3. Mailbox label or read-state changes — optional, not the default
4. Mailbox draft via IMAP APPEND — the Gate 3 tools, kept as their own Scope plus approval. Not bundled into the default triage, and not removed
5. Send — no tool

The Draft view edits the local draft and has no send control.

## Gate 9 — MCP registry (P1)

Lifecycle, not a catalog of servers.

- Record a schema fingerprint at registration. A change requires review. No silent widening of what a server may do
- Isolate credentials per server. They never enter the model, git, the audit body, or a backup
- A tool return value is untrusted: not an instruction, and not an automatic promotion into facts
- Demo target: two or three read-only servers, and one deliberate unauthorized call that is refused

## Gate 10 — deferred (P2)

Long-running tasks and a quiet policy. The name is reserved. No design beyond that is locked, and the gate is not part of P0.

## Gate 11 — deferred (P2)

Specialist workers. A worker would inherit the operator’s budget and policy and would have no independent send. Build it only after one Life Operator is not enough. A peer swarm is out of scope.

## Cross-cutting

These apply in every gate and are not a separate product:

- Python
- LangGraph as the primary orchestrator
- Local, single-host Docker
- Secrets kept out of model context ([SECURITY.md](SECURITY.md))
- Coil as architecture inspiration only (no fork, no vendor, no Coil runtime)
- Nanobot is not the shell
- Pi remains an optional later exec under LangGraph ([ADR-003](DECISIONS.md)). It is not a Gate 6–11 item

## Tracking

Shipping any item updates [STATUS.md](STATUS.md) in the same change. A changed decision is a new ADR in [DECISIONS.md](DECISIONS.md), not an edit to an external page. See [CONTRIBUTING.md](../CONTRIBUTING.md) and [ADR-017](DECISIONS.md).
