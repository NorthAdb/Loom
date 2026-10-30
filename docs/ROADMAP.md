# Roadmap

Locked 2026-10-30. The build order is:

1. Local file sandbox, scheduled routines, and an approval gate
2. Calendar read-only summary and email drafts
3. Deeper browser fill, still with a human confirm

P0–P3 group that order, then the remaining locked follow-ons. Phase labels are a map of those notes. The numbered order above is the constraint. Implementation state lives only in [STATUS.md](STATUS.md).

## P0 — Sandbox, routines, approval

The first loop stays on one machine and does not touch external accounts.

- Local file sandbox
- Scheduled routines
- Approval gate on side effects

Calendar, mail, and browser work wait until these three exist. The memory baseline (Mem0 and a self-owned facts/preferences table) is required for daily recall. It does not jump ahead of the sandbox, routines, and the gate. Agentic memory is P3.

## P1 — Calendar and email drafts

- Calendar read-only summary
- Email draft-only

Sending mail and writing calendar events stay out of scope ([ADR-008](DECISIONS.md)).

## P2 — Browser fill

- Browser fill
- Human confirm before submit or any other remote side effect
- Deepen this path only after P0 and P1 are reliable in daily use

## P3 — Depth, then more scenarios

- Agentic memory, after the Mem0 and facts/preferences baseline exists
- Pi as an optional exec or sub-agent under LangGraph
- Further daily scenarios after the owner is using Loom every day

## Cross-cutting

These apply in every phase and are not a separate product:

- Python
- LangGraph as the primary orchestrator
- Local, single-host Docker
- Secrets kept out of model context ([SECURITY.md](SECURITY.md))
- Coil as architecture inspiration only (no fork, no vendor, no Coil runtime)
- Nanobot is not the shell

## Tracking

Shipping any item updates [STATUS.md](STATUS.md) in the same change. See [CONTRIBUTING.md](../CONTRIBUTING.md).
