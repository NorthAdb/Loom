# Security

Constraints for every later change. The approval gate and the file sandbox boundary below are implemented; the rest is rules the remaining items have to satisfy. [STATUS.md](STATUS.md) is the checklist.

## Secrets

Secrets never enter model context. That includes API keys, tokens, passwords, and mailbox or calendar credentials.

- Do not put secrets in prompts, system messages, tool arguments the model writes, or traces that are fed back to the model.
- Do not commit secrets. `.env` and `.env.*` are gitignored. A placeholder `.env.example` may list variable names only, with empty values.
- Prefer a local secret store or process environment outside the model’s inputs. When a tool needs a credential, the tool reads it. The model sees the result of the call, not the secret.

## Email

Email is draft-only. Loom does not send mail.

The furthest an email tool may go is creating or updating a draft. There is no send tool, and a routine must not add one. A draft is still a side effect: it waits for the approval gate once that gate exists.

## Calendar

Calendar access is a read-only summary. Tools do not create, update, or delete events. A write API is out of scope until a new ADR says otherwise. No such ADR exists.

## Side effects and the approval gate

A human approves side effects before they happen. Side effects include:

- Writes outside the local file sandbox
- Creating or changing an email draft
- Browser actions that submit, click through a commit, or otherwise change remote state
- Any new tool that mutates an external system

Scheduled routines use the same gate. A schedule is a start time, not permission.

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
