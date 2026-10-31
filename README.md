# Loom

Self-hosted personal agent for one person. Daily self-use has to be reliable before the surface area grows. Orchestration is LangGraph. The runtime is local, single-host Docker.

Loom 是自托管的个人代理：先在单机上把每天真正用起来这件事做稳（人工确认、记忆、例行任务），再扩展更多日常场景。编排用 LangGraph，运行在本机 Docker 上。

**Status:** Phase 0 engineering shell, the Phase 1 core loop, the Phase 2 file sandbox, Phase 3 scheduled routines, and the Phase 4 memory baseline are in place (installable package, config, structured logging, `loom version` / `loom doctor`, LangGraph chat loop with an approval gate, audit log, sqlite checkpoints, a jailed local file sandbox with local and HTTP transports, an optional Docker sandbox service, cron-scheduled routines over the same graph with an unattended pending approval queue and `loom worker`, a facts table plus self-hosted Mem0 semantic memory with per-turn note injection and a `loom memory` CLI, tests). Calendar, email, and browser are not started. See [docs/STATUS.md](docs/STATUS.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

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
| Memory | Mem0 + self-owned facts/preferences; agentic memory later | Facts table + self-hosted Mem0 baseline |
| Runtime | Local / single-host Docker | Sandbox service (compose) |
| License | MIT | [LICENSE](LICENSE) |

Dependencies are added only when a roadmap item needs them. The current runtime dependencies are pydantic-settings, LangGraph, the LangChain OpenAI integration, APScheduler, and mem0ai (self-hosted semantic memory).

## How to run

The agent runs: a chat loop over the LangGraph graph with tools, the approval gate for side effects, and audit + checkpoint storage.

```bash
python -m venv .venv
source .venv/bin/activate        # macOS/Linux; Windows Git Bash: source .venv/Scripts/activate
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

### Memory

Memory is two stores with one priority rule. The facts table (`<LOOM_DATA_DIR>/facts.db`) holds authoritative facts and preferences that you manage yourself. Semantic memory is self-hosted Mem0: its vector store and history db live under `<LOOM_DATA_DIR>/memory/`, no Mem0 cloud key is involved, and its telemetry is disabled unless you explicitly set `MEM0_TELEMETRY=true`.

Configuration (both in `.env`):

- `LOOM_MEMORY_ENABLED` — `false` turns the semantic layer off entirely; the graph and CLI then run without any Mem0 endpoint (the facts table keeps working). Default `true`.
- `LOOM_MEMORY_INJECT_LIMIT` — how many memory entries are injected into the model each turn, facts first (default 5).

The endpoint configured through `LLM_BASE_URL` (or the OpenAI default) must serve BOTH chat completions and embeddings: Mem0 uses the chat model to extract memories and the embedding model to index and query them. If the semantic store fails, the turn degrades instead of crashing: a warning is logged and the facts table alone is injected.

```bash
loom memory facts                            # list facts (bare `loom memory` does the same)
loom memory facts set reply_language 中文     # insert or correct one fact
loom memory facts del reply_language         # delete one fact
loom memory search "reply style"             # query the semantic store
```

Table over Mem0: the injected note lists facts-table entries first, labeled 权威 (authoritative), then Mem0 results, labeled 参考 (reference), and tells the model to prefer the facts table on conflict.

Write-back rule: ordinary chat never writes memory. A message is stored only when it uses explicit preference or fact language — the trigger words are 记住, 以后, 总是, 喜欢, 偏好, 不要再, remember, always, prefer, from now on. Anything that looks like a secret (an `sk-`/`pk_`/`ghp_`/`AKIA`/`xox`-prefixed token, or a long token-like string in a message that also contains key/token/secret/password) is refused at both entry paths — `loom memory facts set` and the chat write-back — logged, and never stored.

A no-key demo of the whole baseline (fake semantic store on the same seams, scripted model):

```bash
python scripts/demo_gate3_memory.py
```

记忆：两个存储、一条优先级规则。事实表（`<LOOM_DATA_DIR>/facts.db`）存权威事实与偏好，由你通过 CLI 管理；语义记忆用自托管 Mem0（向量库与历史库在 `<LOOM_DATA_DIR>/memory/` 下，不需要 Mem0 云 key，遥测默认关闭，除非显式设置 `MEM0_TELEMETRY=true`）。`LOOM_MEMORY_ENABLED=false` 可整体关闭语义层（图与 CLI 不再需要 Mem0 端点，事实表照常工作）；`LOOM_MEMORY_INJECT_LIMIT` 控制每轮注入模型的记忆条数（默认 5，事实优先）。注意 `LLM_BASE_URL` 指向的端点必须同时支持 chat completions 与 embeddings：Mem0 用聊天模型抽取记忆、用 embedding 模型建索引与检索；语义层故障时回合降级不崩溃，只记警告并仅注入事实表。注入规则：事实表条目在前、标注 权威，Mem0 结果在后、标注 参考，冲突时以事实表为准。写回规则：普通聊天不写记忆，只有出现明确偏好/事实措辞（记住、以后、总是、喜欢、偏好、不要再、remember、always、prefer、from now on）才写回；疑似密钥（`sk-`/`pk_`/`ghp_`/`AKIA`/`xox` 前缀，或 key/token/secret/password 附近的长 token 串）在 `loom memory facts set` 与聊天写回两条路径都会被拒绝并记日志，绝不入库。试运行：`python scripts/demo_gate3_memory.py`（无需 API key）。

### Sandbox

File tools work only inside the sandbox jail. Two transports, one rule set:

- **Local (default).** Without Docker, Loom runs the jailed dispatcher in-process against `LOOM_SANDBOX_ROOT` (default `./sandbox`, created on demand).
- **Docker (optional).** The same dispatcher runs in a hardened container; Loom reaches it over HTTP:

```bash
docker compose up -d --build    # starts loom-sandbox on 127.0.0.1:8765
```

The container mounts only the sandbox workspace volume (`LOOM_SANDBOX_ROOT`), runs as a non-root user with a read-only root filesystem, and has CPU/memory caps. No `docker.sock`, no home mounts. The mounted directory must be writable by the container's uid 10001, or every HTTP write fails with Permission denied; on a host where the directory is owned by another user, run `chown -R 10001:10001 <dir>` first.

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

### Routines and approvals

A routine is a saved prompt with a cron schedule. Every fire runs through the same compiled LangGraph graph as chat, so the approval gate applies to routines exactly as it does to a person typing. A schedule is a start time, not permission: a routine that reaches a side-effect tool persists a pending approval and stops. Nothing is written until a person runs `loom approvals approve`.

```bash
loom routines add --id daily_sandbox_stats --name "Daily sandbox stats" --cron "30 8 * * *" \
  --prompt "List the sandbox and summarize how many files there are by extension."
loom routines list
loom routines run daily_sandbox_stats
loom routines remove daily_sandbox_stats
loom approvals list
loom approvals approve <pending_id>    # or: loom approvals reject <pending_id>
```

Two ready-to-paste examples:

```bash
# Read-only stats at 08:30 every day; runs without approval.
loom routines add --id daily_sandbox_stats --name "Daily sandbox stats" --cron "30 8 * * *" \
  --prompt "List the sandbox and summarize how many files there are by extension."

# Tidy at 09:00 every day; waits in the pending queue before any file moves.
loom routines add --id daily_tidy --name "Daily tidy" --cron "0 9 * * *" \
  --prompt "Tidy the sandbox: move every file into sorted/<extension>/ by its extension."
```

To fire routines on their schedules, keep `loom worker` running in a terminal or a service:

```bash
loom worker
```

- The worker loads routines once at startup. Routines added while it runs are not hot-reloaded; restart the worker to pick them up.
- `loom routines run <id>` triggers one run immediately through the same path; on a disabled routine it fails with `routine is disabled`.
- Pending records live at `<LOOM_DATA_DIR>/approvals/pending.json`; every trigger and every approve or reject lands in the audit log.

例程与审批：例程是一条带 cron 计划的已保存提示词。每次触发都走与聊天相同的编译后 LangGraph 图，审批闸对例程与人工输入一视同仁。调度只是开始时间、不是放行：例程碰到副作用工具时会写入待批队列并停下，在人执行 `loom approvals approve` 之前不落盘任何东西。`loom routines add|list|run|remove` 管理例程（对已停用例程执行 run 会报 `routine is disabled`），`loom approvals list|approve|reject` 处理待批项，队列在 `<LOOM_DATA_DIR>/approvals/pending.json`；触发与审批决策都写入审计日志。常驻 `loom worker` 按计划触发例程；worker 启动后新增的例程不会热加载，需要重启 worker 才会生效。上面两条可直接粘贴的示例：每日 08:30 只读统计沙箱文件数（直接执行），每日 09:00 整理沙箱（进待批队列等审批）。

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
