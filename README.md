# Loom

Self-hosted personal agent for one person. Daily self-use has to be reliable before the surface area grows. Orchestration is LangGraph. The runtime is local, single-host Docker.

Loom 是自托管的个人代理：先在单机上把每天真正用起来这件事做稳（人工确认、记忆、例行任务），再扩展更多日常场景。编排用 LangGraph，运行在本机 Docker 上。

**Status:** Phase 0 engineering shell and the Phase 1 core loop are in place (installable package, config, structured logging, `loom version` / `loom doctor`, LangGraph chat loop with an approval gate, audit log, sqlite checkpoints, tests). Sandbox, routines, memory, calendar, email, and browser are not started. See [docs/STATUS.md](docs/STATUS.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

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
| Language | Python | Engineering shell, core loop |
| Orchestration | LangGraph | Core loop with approval gate |
| Optional later | Pi as exec / sub-agent | Absent |
| Memory | Mem0 + self-owned facts/preferences; agentic memory later | Absent |
| Runtime | Local / single-host Docker | Absent |
| License | MIT | [LICENSE](LICENSE) |

Dependencies are added only when a roadmap item needs them. The current runtime dependencies are pydantic-settings, LangGraph, and the LangChain OpenAI integration.

## How to run

The engineering shell installs and runs. There is no agent behind it yet.

```bash
python -m venv .venv
source .venv/Scripts/activate    # Windows Git Bash; macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env             # then edit .env and set LOOM_ENV=dev
```

With the virtualenv active:

```bash
loom version    # prints the package version, needs no config or .env
loom doctor     # one line per check; exits 1 while a critical check fails
pytest          # runs the test suite
```

`loom doctor` fails on a missing `.env`, a broken config (the message names the variable, e.g. `LOOM_ENV`), or a data directory it cannot create or write. A missing `LLM_API_KEY` is only a warning in Phase 0.

### Chat

Set `LLM_API_KEY` in `.env` (optionally `LLM_MODEL` and `LLM_BASE_URL` for a non-default model or an OpenAI-compatible endpoint), then run:

```bash
loom chat
```

Each session prints a thread id. Harmless tools (`get_time`, `echo`) run directly. A side-effect tool (`propose_sandbox_note`) pauses with the proposed tool and arguments and asks `approve or reject?`. `approve` runs the tool; `reject` does not. Both paths append records to `<LOOM_DATA_DIR>/audit/YYYYMMDD.jsonl`.

To continue a previous session, rerun with its thread id; the conversation resumes from its checkpoint:

```bash
loom chat --thread-id <id>
```

Audit lines and checkpoints live under `LOOM_DATA_DIR`.

聊天：在 `.env` 设置 `LLM_API_KEY`（可选 `LLM_MODEL`、`LLM_BASE_URL`），运行 `loom chat`。无害工具（`get_time`、`echo`）直接执行；副作用工具（`propose_sandbox_note`）会暂停并列出待批工具与参数，等待 `approve or reject?`：approve 执行、reject 不执行，两条路径都写入 `<LOOM_DATA_DIR>/audit/YYYYMMDD.jsonl`。用 `loom chat --thread-id <id>` 从检查点续跑同一会话；审计与检查点都在 `LOOM_DATA_DIR` 下。

从源码安装：建 venv 并 `pip install -e ".[dev]"`，复制 `.env.example` 为 `.env`，把 `LOOM_ENV` 设为 `dev`，然后运行 `loom version` 与 `loom doctor`。doctor 对缺失 `.env`、非法配置（错误信息会指名变量，如 `LOOM_ENV`）、不可写的数据目录判失败；`LLM_API_KEY` 在 Phase 0 缺失只是警告，不影响退出码。

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
