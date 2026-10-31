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
- The facts table is human-authored: rows enter through the CLI, not from model output.
- With Mem0 enabled, chat text is sent to the configured LLM/embedding endpoints: Mem0 uses the chat model to extract memories and the embedding model to index and query them, so a message that passes the secret guards still travels to those endpoints (self-hosted, or the OpenAI default when `LLM_BASE_URL` is empty).
- Memory reads and writes are not side effects: they pass through no approval gate and appear nowhere in the audit log, and audit records never contain memory contents.

## Email

Email is draft-only. Loom does not send mail.

Implemented (Phase 5, reworked): `mail_create_draft` and `mail_update_draft` are the only mail tools. They APPEND a draft with the `\Draft` flag to the configured IMAP folder (`LOOM_MAIL_FOLDER`, default Drafts, validated to be a drafts folder — `INBOX` and folders whose leaf segment is `Sent`, `Trash`, `Junk`, or `Deleted` are refused) or replace one through a UID-only flow: UID SEARCH must hit exactly one message, a UID FETCH of that message must confirm the Message-ID exactly and the `\Draft` flag before anything is written, then the replacement is appended and only that one UID is marked `\Deleted` and expunged by UID (`UID EXPUNGE` exists only when the server advertises UIDPLUS; otherwise the old draft stays on the server and the result says so). Bare `STORE`/`SEARCH`/`EXPUNGE` are never used, so unrelated deleted mail survives. Both tools are `side_effect=true`, so a draft waits for the approval gate; the approval payload shows the full recipient, subject, and body (a draft whose to+subject+body exceeds 10000 characters is refused before approval and audited as rejected), and a rejected draft never reaches the IMAP server. There is no send tool, and a routine must not add one: `tests/test_gate3_guards.py` greps `src/loom` for send symbols and allows only the UID draft and read IMAP verbs (uid, append, select, login, logout).

## Calendar

Calendar access is a read-only summary. Implemented (Phase 5, reworked): `calendar_list_events` and `calendar_summarize_range` (both `side_effect=false`) GET an ICS feed over **https only** (plain http is accepted only for loopback hosts, which tests and demos serve; redirects that leave https are refused; the response body is capped at 1 MiB) and render events in the configured timezone. Every transport failure becomes one `RuntimeError` built from a URL redacted to scheme and host — the feed URL's private token in its path or query is never sent over plain http nor echoed in an error, and the original exception is never chained. Neither the transports nor `parse_events` can create, update, or delete an event, and the same gate test fails if a write symbol appears in `src/loom`. A write API is out of scope until a new ADR says otherwise. No such ADR exists.

## Side effects and the approval gate

A human approves side effects before they happen. Side effects include:

- Writes outside the local file sandbox
- Creating or changing an email draft
- Browser actions that submit, click through a commit, or otherwise change remote state
- Any new tool that mutates an external system

Scheduled routines use the same gate. A schedule is a start time, not permission. An unattended routine run that reaches the gate persists a pending record in `<data dir>/approvals/pending.json` and stops before anything executes. A person resolves it explicitly with `loom approvals approve` or `loom approvals reject`; the worker has no auto-approve mode.

Filling a browser form may proceed up to the point of confirmation. The confirm is a person, not the model approving itself.

## Files

Local file work stays inside a sandbox on the single host. The boundary is implemented and described in the next section.

## Sandbox boundary (implemented)

Enforced by `src/loom/sandbox.py`, used by both the local in-process transport and the HTTP transport:

- Path jail: every path argument is resolved inside the sandbox root; `../`, absolute paths outside the jail, and symlinks resolving outside are rejected with an error, and nothing is written.
- Read cap: `read_file` refuses files above 1 MiB.
- Approval gate: `write_file`, `mkdir`, `move_file`, and `delete_file` are side-effect tools and pause for a human; `list_dir` and `read_file` run without approval. Every decision and execution is audited.
- Container (`Dockerfile` + `compose.yml`): runs as non-root uid 10001, read-only root filesystem, only the workspace volume mounted, no docker.sock, no home mounts, mem_limit 256m, cpus 0.5, no-new-privileges.

Not yet: network egress restriction on the sandbox container, per-user or per-day quotas on file operations.

## What this document is

A constraint on future code. It is not evidence that controls exist in the tree today.
