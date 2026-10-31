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
- Memory reads and writes are not side effects: they pass through no approval gate and appear nowhere in the audit log, and audit records never contain memory contents.

## Email

Email is draft-only. Loom does not send mail.

Implemented (Phase 5): `mail_create_draft` and `mail_update_draft` are the only mail tools. They APPEND a draft with the `\Draft` flag to the configured IMAP folder (`LOOM_MAIL_FOLDER`, default Drafts) or replace one by Message-ID search, and nothing else in the module touches the server. Both are `side_effect=true`, so a draft waits for the approval gate; the approval payload shows the full recipient, subject, and body, and a rejected draft never reaches the IMAP server. There is no send tool, and a routine must not add one: `tests/test_gate3_guards.py` greps `src/loom` for send symbols and allows only the draft and read IMAP verbs (append, store, search, expunge, select, login, logout).

## Calendar

Calendar access is a read-only summary. Implemented (Phase 5): `calendar_list_events` and `calendar_summarize_range` (both `side_effect=false`) GET an ICS feed and render events in the configured timezone. Neither the transports nor `parse_events` can create, update, or delete an event, and the same gate test fails if a write symbol appears in `src/loom`. A write API is out of scope until a new ADR says otherwise. No such ADR exists.

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
