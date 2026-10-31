# Status

Source of truth for what this repository contains. Update this file in the same change that moves an item. Rules: [CONTRIBUTING.md](../CONTRIBUTING.md). Plan: [ROADMAP.md](ROADMAP.md).

Last updated: 2026-10-31 (Phase 5 calendar + email drafts: read-only ICS calendar summaries, IMAP draft-only mail tools behind the approval gate, no send path, gate tests).

## Done

- [x] Repository bootstrap: README, vision, architecture, decisions, roadmap, status, security, contributing, MIT license, Python package skeleton (`loom` version marker only)
- [x] Phase 0 engineering foundation: installable package (`pip install -e ".[dev]"`), pydantic-settings config with `.env` support and errors that name the variable, structured JSON logging with secret redaction, `loom version` and `loom doctor` CLI, pytest suite
- [x] Real “how to run” instructions in README
- [x] LangGraph orchestration
- [x] Approval gate for side effects
- [x] Phase 1 core loop: `loom chat` REPL, tool registry with side-effect flags, interrupt-based approval gate, append-only JSONL audit, sqlite checkpoints, mock-model-tested
- [x] Phase 2 file sandbox: jailed sandbox dispatcher (path jail, 1 MiB read cap), local in-process and HTTP transports, six file tools (`list_dir` / `read_file` unapproved; `write_file` / `mkdir` / `move_file` / `delete_file` behind the gate), compose service with non-root user, read-only root filesystem and resource caps, `fixtures/sandbox_messy/`, tidy demo `scripts/demo_gate2.py`, doctor sandbox check
- [x] Local file sandbox
- [x] Local single-host Docker runtime (sandbox service; the agent loop still runs on the host)
- [x] Phase 3 scheduled routines: routine store (`routines.json`, audited mutations, cron validated by `CronTrigger.from_crontab`), runner over the same compiled graph as chat (`thread_id` = `routine:{id}:{ts}`), unattended pending approval queue (`approvals/pending.json`) that stops before any write, `loom routines add|list|remove|run` and `loom approvals list|approve|reject` CLI, APScheduler `loom worker` (no hot reload), accelerated schedule-trigger test, demo `scripts/demo_gate2_routines.py`
- [x] Phase 4 memory baseline: facts table (`FactsStore` over `facts.db` with upsert/get/list/delete, secret-bearing keys and values refused), self-hosted Mem0 semantic memory behind a `SemanticMemory` protocol (`NullSemanticMemory` when `LOOM_MEMORY_ENABLED=false`, telemetry off), `pre_agent` memory-note injection (facts 权威 first, Mem0 参考 after, table wins on conflict, capped by `LOOM_MEMORY_INJECT_LIMIT`), `post_turn` write-back only on explicit preference/fact language with a secret guard and logged refusal, `loom memory facts [set|del]` and `loom memory search` CLI, demo `scripts/demo_gate3_memory.py`
- [x] Memory baseline: Mem0
- [x] Memory baseline: self-owned facts and preferences table
- [x] Phase 5 calendar + email drafts: read-only calendar summaries (`HttpCalendarTransport` GETs the `LOOM_ICAL_URL` ICS feed, `parse_events` clamps to a window in `LOOM_TIMEZONE` (default Asia/Hong_Kong), expands simple RRULEs with a 200-occurrence cap and skips malformed events, `calendar_list_events` today/tomorrow/week and `calendar_summarize_range` tools, side_effect=false), draft-only mail (`mail_create_draft` / `mail_update_draft`, side_effect=true, append with \Draft to `LOOM_MAIL_FOLDER` over IMAP4_SSL, replace by Message-ID search + \Deleted + expunge, the approval payload shows the full recipient/subject/body while audit lines stay capped at 200 chars, reject means the IMAP server is never called), gate tests proving the registry flags, no send / calendar-write symbols in `src/loom`, the IMAP verb allowlist, and no password in tool args, doctor checks for the ICS feed and the IMAP host, demo `scripts/demo_gate3_calendar_mail.py`
- [x] Calendar read-only summary
- [x] Email draft-only (no send path)

## In progress

- None

## Not started

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
