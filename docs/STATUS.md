# Status

Source of truth for what this repository contains. Update this file in the same change that moves an item. Rules: [CONTRIBUTING.md](../CONTRIBUTING.md). Plan: [ROADMAP.md](ROADMAP.md).

Last updated: 2026-10-31 (Gate 1 materials: Phase 0 + Phase 1 merged).

## Done

- [x] Repository bootstrap: README, vision, architecture, decisions, roadmap, status, security, contributing, MIT license, Python package skeleton (`loom` version marker only)
- [x] Phase 0 engineering foundation: installable package (`pip install -e ".[dev]"`), pydantic-settings config with `.env` support and errors that name the variable, structured JSON logging with secret redaction, `loom version` and `loom doctor` CLI, pytest suite
- [x] Real “how to run” instructions in README
- [x] LangGraph orchestration
- [x] Approval gate for side effects
- [x] Phase 1 core loop: `loom chat` REPL, tool registry with side-effect flags, interrupt-based approval gate, append-only JSONL audit, sqlite checkpoints, mock-model-tested

## In progress

- None

## Not started

### P0 — Sandbox, routines, approval

- [ ] Local file sandbox
- [ ] Scheduled routines
- [ ] Memory baseline: Mem0
- [ ] Memory baseline: self-owned facts and preferences table
- [ ] Local single-host Docker runtime

### P1 — Calendar and email drafts

- [ ] Calendar read-only summary
- [ ] Email draft-only (no send path)

### P2 — Browser fill

- [ ] Browser fill
- [ ] Human confirm before browser side effects

### P3 — Depth, then more scenarios

- [ ] Agentic memory
- [ ] Optional Pi exec / sub-agent
- [ ] Further daily scenarios beyond the locked list

### Standing constraints (not features)

These are decisions, not tasks to “finish.” They stay in force while the items above are built. See [DECISIONS.md](DECISIONS.md) and [SECURITY.md](SECURITY.md).

- Secrets stay out of model context and git
- Email never sends
- Calendar stays read-only
- Coil is not forked, vendored, or used as the runtime
- Nanobot is not the shell
