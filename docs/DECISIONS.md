# Decisions

Choices locked on 2026-10-30. The repository is the source of truth for what is done. This file records intent. [STATUS.md](STATUS.md) records implementation.

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

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** The memory baseline is Mem0 plus a self-owned facts and preferences table. Agentic memory comes after that baseline exists.
- **Consequences:** Facts the owner cares about have a store Loom owns, separate from Mem0. Agentic memory is not a shortcut around that table.

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

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** Build order is file sandbox, scheduled routines, and an approval gate, then calendar and email drafts, then a deeper browser-fill path.
- **Consequences:** [ROADMAP.md](ROADMAP.md) follows that sequence. External-account work does not precede the sandbox, routines, and the gate.

## ADR-010 — Repository state tracks progress

- **Status:** Accepted
- **Date:** 2026-10-30
- **Decision:** The repository is the source of truth for what is done and what is not. Docs, especially [STATUS.md](STATUS.md), move in the same change as the work they describe.
- **Consequences:** A feature is not done because a chat or a roadmap bullet says so. [CONTRIBUTING.md](../CONTRIBUTING.md) requires the status update.
