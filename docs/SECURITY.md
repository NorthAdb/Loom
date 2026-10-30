# Security

Constraints for every later change. Nothing in this file is implemented yet. [STATUS.md](STATUS.md) is the checklist; this file is the rule set those items have to satisfy.

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

Local file work stays inside a sandbox on the single host. The sandbox is not built yet. Until it exists, do not add tools that read or write the host filesystem.

## What this document is

A constraint on future code. It is not evidence that controls exist in the tree today.
