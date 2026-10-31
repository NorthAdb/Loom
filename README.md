# Loom

Self-hosted personal agent for one person. Daily self-use has to be reliable before the surface area grows. Orchestration is LangGraph. The runtime is local, single-host Docker.

Loom 是自托管的个人代理：先在单机上把每天真正用起来这件事做稳（人工确认、记忆、例行任务），再扩展更多日常场景。编排用 LangGraph，运行在本机 Docker 上。

**Status:** Phase 0 engineering shell, the Phase 1 core loop, and the Phase 2 file sandbox are in place (installable package, config, structured logging, `loom version` / `loom doctor`, LangGraph chat loop with an approval gate, audit log, sqlite checkpoints, a jailed local file sandbox with local and HTTP transports, an optional Docker sandbox service, tests). Routines, memory, calendar, email, and browser are not started. See [docs/STATUS.md](docs/STATUS.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

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

The LangGraph loop and the sandbox layer are built; the memory layer and MCP are not. Full notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Security rules: [docs/SECURITY.md](docs/SECURITY.md).

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
| Runtime | Local / single-host Docker | Sandbox service (compose) |
| License | MIT | [LICENSE](LICENSE) |

Dependencies are added only when a roadmap item needs them. The current runtime dependencies are pydantic-settings, LangGraph, and the LangChain OpenAI integration.

## How to run

The agent runs: a chat loop over the LangGraph graph with tools, the approval gate for side effects, and audit + checkpoint storage.

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

`loom doctor` fails on a missing `.env`, a broken config (the message names the variable, e.g. `LOOM_ENV`), or a data directory it cannot create or write. A missing `LLM_API_KEY` is a warning; `loom chat` needs it.

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

从源码安装：建 venv 并 `pip install -e ".[dev]"`，复制 `.env.example` 为 `.env`，把 `LOOM_ENV` 设为 `dev`，然后运行 `loom version` 与 `loom doctor`。doctor 对缺失 `.env`、非法配置（错误信息会指名变量，如 `LOOM_ENV`）、不可写的数据目录判失败；`LLM_API_KEY` 缺失只是警告，但 `loom chat` 必须设置它。

### Sandbox

File tools work only inside the sandbox jail. Two transports, one rule set:

- **Local (default).** Without Docker, Loom runs the jailed dispatcher in-process against `LOOM_SANDBOX_ROOT` (default `./sandbox`, created on demand).
- **Docker (optional).** The same dispatcher runs in a hardened container; Loom reaches it over HTTP:

```bash
docker compose up -d --build    # starts loom-sandbox on 127.0.0.1:8765
```

The container mounts only the sandbox workspace volume (`LOOM_SANDBOX_ROOT`), runs as a non-root user with a read-only root filesystem, and has CPU/memory caps. No `docker.sock`, no home mounts.

Configuration (both go in `.env`):

- `LOOM_SANDBOX_ROOT` — host directory every file op is jailed to (default `./sandbox`)
- `LOOM_SANDBOX_URL` — sandbox service URL, e.g. `http://127.0.0.1:8765`; empty means the local in-process transport

File tools and approval:

| Tool | Pauses for approval |
| --- | --- |
| `list_dir`, `read_file` (1 MiB cap) | no |
| `write_file`, `mkdir`, `move_file`, `delete_file` | yes |

Paths that leave the sandbox (`../`, absolute paths outside the jail, symlinks pointing out) are rejected by the jail itself, with or without approval. A demo that tidies the fixtures through the full loop without Docker or an API key:

```bash
python scripts/demo_gate2.py
```

沙箱：文件工具只作用于沙箱内。默认无 Docker 时用本地进程内的路径 jail（根目录 `LOOM_SANDBOX_ROOT`，默认 `./sandbox`，自动创建）；可选 `docker compose up -d --build` 启动加固容器（只挂载工作区卷、非 root、只读根文件系统、CPU/内存限额，无 docker.sock、不挂 home），通过 `LOOM_SANDBOX_URL`（如 `http://127.0.0.1:8765`）访问。六个文件工具中 `list_dir`、`read_file`（1 MiB 上限）直接执行，`write_file`、`mkdir`、`move_file`、`delete_file` 一律暂停等审批；路径逃逸（`../`、指向 jail 外的绝对路径或符号链接）由 jail 直接拒绝。试运行：`python scripts/demo_gate2.py`（整理 fixtures，无需 Docker 与 API key）。

Example prompt / 示例提示词：

```text
把沙箱里的文件按扩展名整理到 sorted/<ext>/ 目录
```

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
