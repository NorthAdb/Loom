# Loom

Self-hosted personal agent for one person. Daily self-use has to be reliable before the surface area grows. Orchestration is LangGraph. The runtime is local, single-host Docker.

Loom 是自托管的个人代理：先在单机上把每天真正用起来这件事做稳（人工确认、记忆、例行任务），再扩展更多日常场景。编排用 LangGraph，运行在本机 Docker 上。

**Status:** repository bootstrap only. The agent runtime is not implemented. See [docs/STATUS.md](docs/STATUS.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

## What Loom is

Loom is a deep-and-wide personal agent the owner can run every day on one machine. Early work is the loop that makes that plausible: a local file sandbox, scheduled routines, and a human approval gate. Calendar summaries, email drafts, and browser fill come after that loop exists.

Product boundaries and the 2026-10-30 decisions live in [docs/VISION.md](docs/VISION.md) and [docs/DECISIONS.md](docs/DECISIONS.md).

## Non-goals

- A hosted multi-user product
- Cloudflare Coil as the runtime, or a fork or vendored copy of Coil (Coil is architecture inspiration only)
- Nanobot as the main shell
- Sending email (drafts only)
- Creating, editing, or deleting calendar events (read-only summaries)
- Browser actions that change remote state without a human confirm
- Secrets in model context, prompts, or git

## Architecture

Target shape, none of it built yet. Full notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Security rules: [docs/SECURITY.md](docs/SECURITY.md).

```text
person / scheduler
        │
        ▼
   LangGraph  ──►  memory (Mem0 + owned facts/preferences)
        │
        ▼
 approval gate
        │
        ▼
  tools / MCP  ──►  local Docker sandbox
```

Pi may later sit under LangGraph as an exec or sub-agent. It is not part of the skeleton.

## Stack

| Piece | Choice | In this tree |
| --- | --- | --- |
| Language | Python | Package skeleton only |
| Orchestration | LangGraph | Not wired |
| Optional later | Pi as exec / sub-agent | Absent |
| Memory | Mem0 + self-owned facts/preferences; agentic memory later | Absent |
| Runtime | Local / single-host Docker | Absent |
| License | MIT | [LICENSE](LICENSE) |

Dependencies are intentionally empty until a roadmap item needs them.

## How to run

There is no agent to run. There is no Docker image, CLI, graph, or tool server.

The package is a version marker:

```bash
PYTHONPATH=src python -c "import loom; print(loom.__version__)"
```

Replace this section when a real entrypoint exists, and update [docs/STATUS.md](docs/STATUS.md) in the same change.

## Docs

- [docs/VISION.md](docs/VISION.md) — vision and boundaries
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — target layers
- [docs/DECISIONS.md](docs/DECISIONS.md) — locked choices
- [docs/ROADMAP.md](docs/ROADMAP.md) — P0–P3
- [docs/STATUS.md](docs/STATUS.md) — what the repository actually contains
- [docs/SECURITY.md](docs/SECURITY.md) — secrets, drafts, approval
- [CONTRIBUTING.md](CONTRIBUTING.md) — status updates are part of shipping

## License

[MIT](LICENSE). Copyright (c) 2026 NorthAdb.
