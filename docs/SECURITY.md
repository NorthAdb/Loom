# Security

Constraints for every later change. The approval gate, the file sandbox boundary, the memory secret guards, and the email draft and read-only calendar paths below are implemented; the rest is rules the remaining items have to satisfy. [STATUS.md](STATUS.md) is the checklist.

## Secrets

Secrets never enter model context. That includes API keys, tokens, passwords, and mailbox or calendar credentials.

- Do not put secrets in prompts, system messages, tool arguments the model writes, or traces that are fed back to the model.
- Do not commit secrets. `.env` and `.env.*` are gitignored. A placeholder `.env.example` may list variable names only, with empty values.
- Prefer a local secret store or process environment outside the model’s inputs. When a tool needs a credential, the tool reads it. The model sees the result of the call, not the secret.

## Memory

Memory has two entry paths and both refuse secrets; nothing that looks like a key or token is ever stored.

- `loom memory facts set` refuses a key whose name looks secret-bearing and a value that looks like an API key or token: a provider-style `sk-`/`pk_`/`ghp_`/`AKIA`/`xox` prefix, or a token-like run of 24 or more `[A-Za-z0-9_-]` characters in a message that also contains key/token/secret/password. The refusal is a `ValueError` the CLI prints.
- The chat write-back (`post_turn`) stores a user message only when it uses explicit preference or fact language (记住, 以后, 总是, 喜欢, 偏好, 不要再, remember, always, prefer, from now on); ordinary chat writes nothing. Before storing, the same secret shape is checked, the write is refused, and the refusal is logged and appended to the conversation — the secret itself is never persisted.
- The facts table is human-authored: rows enter through the CLI, not from model output. That remains the rule for the tree today. From Gate 6, untrusted inbound must not use this path and must not silently upsert facts. That split is not implemented yet (ADR-013).
- With Mem0 enabled, chat text is sent to the configured LLM/embedding endpoints: Mem0 uses the chat model to extract memories and the embedding model to index and query them, so a message that passes the secret guards still travels to those endpoints (self-hosted, or the OpenAI default when `LLM_BASE_URL` is empty).
- Memory reads and writes are not side effects in the tree today: they pass through no approval gate and appear nowhere in the audit log, and audit records never contain memory contents. Gate 6 forbids a silent fact upsert from inbound. Once Request Scope exists (Gate 7, ADR-014), an inbound memory write is a scoped action, not an extension of this exemption. Neither control is in the tree.

## Email

Email is draft-only. Loom does not send mail.

Implemented (Phase 5, reworked): `mail_create_draft` and `mail_update_draft` are the only mail tools. They APPEND a draft with the `\Draft` flag to the configured IMAP folder (`LOOM_MAIL_FOLDER`, default Drafts, validated to be a drafts folder — `INBOX` and folders whose leaf segment is `Sent`, `Trash`, `Junk`, or `Deleted` are refused) or replace one through a UID-only flow: UID SEARCH must hit exactly one message, a UID FETCH of that message must confirm the Message-ID exactly and the `\Draft` flag before anything is written, then the replacement is appended and only that one UID is marked `\Deleted` and expunged by UID (`UID EXPUNGE` exists only when the server advertises UIDPLUS; otherwise the old draft stays on the server and the result says so). Bare `STORE`/`SEARCH`/`EXPUNGE` are never used, so unrelated deleted mail survives. Both tools are `side_effect=true`, so a draft waits for the approval gate; the approval payload shows the full recipient, subject, and body (a draft whose to+subject+body exceeds 10000 characters is refused before approval and audited as rejected), and a rejected draft never reaches the IMAP server. There is no send tool, and a routine must not add one: `tests/test_gate3_guards.py` greps `src/loom` for send symbols and allows only the UID draft and read IMAP verbs (uid, append, select, login, logout).

Gate 8 layering is not built. When it is, the default triage write is a local draft, not this IMAP path. Label and read-state changes are optional and are not the default. The IMAP APPEND tools stay, as their own scope plus the approval gate above, and they are not folded into that default. Send stays unregistered. The future Draft view has no send control (ADR-016). `send_permission` is false on every Request Scope (ADR-014).

## Calendar

Calendar access is a read-only summary. Implemented (Phase 5, reworked): `calendar_list_events` and `calendar_summarize_range` (both `side_effect=false`) GET an ICS feed over **https only** (plain http is accepted only for loopback hosts, which tests and demos serve; redirects that leave https are refused; the response body is capped at 1 MiB) and render events in the configured timezone. Every transport failure becomes one `RuntimeError` built from a URL redacted to scheme and host — the feed URL's private token in its path or query is never sent over plain http nor echoed in an error, and the original exception is never chained. Neither the transports nor `parse_events` can create, update, or delete an event, and the same gate test fails if a write symbol appears in `src/loom`. A write API is out of scope until a new ADR says otherwise. No such ADR exists.

## MCP servers

MCP servers join the same gates. Implemented (Gate 9, ADR-020): `loom mcp register` probes the stdio server once and records a schema fingerprint (sha256 over the canonical JSON of the tool list — key order and whitespace never matter, any reviewed byte does) plus an explicit allowed tool subset in `<data dir>/mcp_servers.json`; a forced re-registration freezes the record until `loom mcp review` re-probes it. Every call carries the action name `mcp:<server_id>:<tool>`, runs `side_effect=True` through the Tool Gateway, and is re-verified inside the bridge at call time — unregistered server, out-of-subset tool, frozen server, or drifted fingerprint refuses with an audited rejection. The client is an in-house JSON-RPC 2.0-over-stdio implementation with a 1 MiB response cap and no SDK; server stderr is drained and discarded because it can carry secrets and never enters exceptions or logs.

Credentials are env-var handles: the registry stores the variable NAME only, the client maps that name to its current value in the child's environment, and the value never enters the model, git, the audit body, or a backup (`mcp_servers.json` is pinned out of `BACKUP_ROOTS`). Tool return values are untrusted data — they reach the model as text and write nothing: no facts, no scope widening, `send_permission` untouched. MCP is off by default; an entrypoint enables it deliberately (`open_graph(mcp=True)` or an explicit `extra_registry`).

## Side effects and the approval gate

A human approves side effects before they happen. Side effects include:

- Writes outside the local file sandbox
- Creating or changing an email draft
- Browser actions that submit, click through a commit, or otherwise change remote state
- Any new tool that mutates an external system

Scheduled routines use the same gate. A schedule is a start time, not permission. An unattended routine run that reaches the gate persists a pending record in `<data dir>/approvals/pending.json` and stops before anything executes. A person resolves it explicitly with `loom approvals approve` or `loom approvals reject`; the worker has no auto-approve mode.

Filling a browser form may proceed up to the point of confirmation. The confirm is a person, not the model approving itself.

Browser navigation is a transport boundary. Every url — the one the tools open, the one a click will use (the control's `formaction`, else its owning form's action), and every request the page itself makes — must be loopback or listed in `LOOM_BROWSER_ALLOWED_HOSTS` (empty means loopback only). A request firewall aborts anything else before it is sent, and a submission it blocks raises instead of ever reading as accepted. Redirect responses are followed by the browser below the interception layer, so a navigation that lands off the allowlist closes the page before the model sees anything; a 307/308 redirect could still re-send a request body before that close, a known limit of the interception layer. Password fields never leave the browser: what the model and the approval see reads `***` (empty when unfilled). The submit approval shows the page's real url and field values read back from the browser, merged over anything the model claims, so a person approves what the browser holds, not what the model reports.

## Files

Local file work stays inside a sandbox on the single host. The boundary is implemented and described in the next section.

## Sandbox boundary (implemented)

Enforced by `src/loom/sandbox.py`, used by both the local in-process transport and the HTTP transport:

- Path jail: every path argument is resolved inside the sandbox root; `../`, absolute paths outside the jail, and symlinks resolving outside are rejected with an error, and nothing is written.
- Read cap: `read_file` refuses files above 1 MiB.
- Approval gate: `write_file`, `mkdir`, `move_file`, and `delete_file` are side-effect tools and pause for a human; `list_dir` and `read_file` run without approval. Every decision and execution is audited.
- Container (`Dockerfile` + `compose.yml`): runs as non-root uid 10001, read-only root filesystem, only the workspace volume mounted, no docker.sock, no home mounts, mem_limit 256m, cpus 0.5, no-new-privileges.

Not yet: network egress restriction on the sandbox container, per-user or per-day quotas on file operations.

## Gate 6+ constraints (not implemented)

These bind the work in [ROADMAP.md](ROADMAP.md). None of them is evidence that the control exists today.

- Request Scope is checked outside the model. The model cannot widen a scope. `send_permission` is false on every scope.
- An email From header, or a body that claims to be the owner, is not an authenticated principal.
- Mail, HTML, attachments, and tool return values are untrusted data. They are not instructions. The path that reads them does not gain the right to write facts.
- Reading content, following an instruction, writing a fact, and changing a permission stay separate.
- If a side-effect authorization or its audit write fails, the action does not run (fail closed).
- The audit log stays append-only JSONL. A `correlation_id` makes a draft traceable back to its scope. The log is not a tamper-proof proof. A hash chain is deferred.
- The Operator Console, when it exists, binds locally, checks CSRF and Origin/Host, and accepts an approval only from a session. A hidden button is not an approval.

## What this document is

The sections marked implemented describe controls in the tree. Everything else, including the Gate 6+ list above, is a constraint on future code. It is not evidence that the control exists today.
