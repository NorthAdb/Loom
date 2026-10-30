# Vision

Loom is a personal agent one person can run every day. Depth and breadth both matter, and the order is fixed: make self-use reliable, then add daily scenarios.

The owner is the first user. A scenario counts as real only after the repository contains it and the owner can repeat it. [STATUS.md](STATUS.md) is the checklist. A paragraph in this file is not an implementation.

Locked 2026-10-30. Rationale: [DECISIONS.md](DECISIONS.md). Build order: [ROADMAP.md](ROADMAP.md).

## Intent

- **Deep and wide.** One agent that can take on more of a day, not a pile of one-off scripts. Width waits on depth: a narrow loop that is trustworthy beats a wide loop that is not.
- **Self-use first.** Design and sequencing favor the owner running Loom every day. Extra scenarios are earned by that habit.
- **One host.** Execution is local Docker on a single machine. The person can see where files, secrets, and side effects sit.
- **A person in the loop.** Anything that changes the world stops for approval. Routines do not skip that stop.

## Scenarios

These are the target scenarios. They are not started.

1. Local file sandbox
2. Scheduled routines
3. Approval gate for side effects (required with the two above)
4. Calendar read-only summary
5. Email draft-only
6. Browser fill, with a human confirm before a side effect

The first three are the P0 loop. Calendar and email drafts follow. Browser fill is deepened only after that earlier work is reliable.

## Boundaries

- Calendar tools summarize. They do not create, update, or delete events.
- Email tools stop at a draft. They do not send.
- Browser fill may prepare a page. Submit and other remote side effects wait for an explicit human confirm.
- File work stays inside a local sandbox.
- Memory starts as Mem0 plus a self-owned facts and preferences table. Agentic memory is a later layer on that baseline.
- LangGraph is the orchestrator. Pi is an optional exec or sub-agent later, under LangGraph.
- Secrets stay out of model context.

## Non-goals

- A hosted or multi-tenant service
- Cloudflare Coil as the runtime
- Forking or vendoring Coil (Coil may inform the architecture; it is not a dependency)
- Nanobot as the main shell
- Unattended sending, calendar writes, or browser submission
