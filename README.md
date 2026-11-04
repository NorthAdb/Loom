# Loom

Self-hosted personal agent for one person. Daily self-use has to be reliable before the surface area grows. Orchestration is LangGraph. The runtime is local, single-host Docker.

Loom 是自托管的个人代理：先在单机上把每天真正用起来这件事做稳（人工确认、记忆、例行任务），再扩展更多日常场景。编排用 LangGraph，运行在本机 Docker 上。

**Current:** Gate 1–5 are done on main tip `1711d1b`. Next is Gate 6 (Context OS, including Task State). [docs/STATUS.md](docs/STATUS.md) · [docs/ROADMAP.md](docs/ROADMAP.md).

当前：Gate 1–5 已完成（main `1711d1b`），下一关是 Gate 6。进度见 [docs/STATUS.md](docs/STATUS.md)，计划见 [docs/ROADMAP.md](docs/ROADMAP.md)。

**Status:** Phase 0 engineering shell, the Phase 1 core loop, the Phase 2 file sandbox, Phase 3 scheduled routines, the Phase 4 memory baseline, the Phase 5 calendar + email drafts, the Phase 6 browser fill, and the Phase 7 budget, backup, eval, and CI are in place (installable package, config, structured logging, `loom version` / `loom doctor`, LangGraph chat loop with an approval gate, audit log, sqlite checkpoints, a jailed local file sandbox with local and HTTP transports, an optional Docker sandbox service, cron-scheduled routines over the same graph with an unattended pending approval queue and `loom worker`, a facts table plus self-hosted Mem0 semantic memory with per-turn note injection and a `loom memory` CLI, read-only ICS calendar summaries and draft-only IMAP mail tools behind the approval gate, browser fill over headless Playwright chromium where open and fill prepare a page unapproved and submit waits for a human, tests). See [docs/STATUS.md](docs/STATUS.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

## What Loom is

Loom is a personal agent the owner runs every day on one machine. Gate 1–5 is that loop in the tree: a local file sandbox, scheduled routines, a human approval gate, a memory baseline, calendar summaries, email drafts, and browser fill. The next bet is one Life Operator — untrusted inbound in, a gated life action out — and it starts at Gate 6. It is not built yet.

Product boundaries and the locked decisions (2026-10-30, extended 2026-11-04) live in [docs/VISION.md](docs/VISION.md) and [docs/DECISIONS.md](docs/DECISIONS.md).

## Non-goals

- A hosted multi-user product
- Cloudflare Coil as the runtime, or a fork or vendored copy of Coil (Coil is architecture inspiration only)
- Nanobot as the main shell
- Sending email (drafts only)
- Creating, editing, or deleting calendar events (read-only summaries)
- Browser actions that change remote state without a human confirm
- Secrets in model context, prompts, git, the audit log, or backups
- Treating inbound content as instructions, or an email From header as the owner
- A thick RAG store as the memory spine

## Architecture

The LangGraph loop, the sandbox layer, and the memory baseline are built; MCP, Request Scope, and the Operator Console are not. Full notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Security rules: [docs/SECURITY.md](docs/SECURITY.md).

Built through Gate 5:

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
| Memory | Facts table plus optional Mem0; light Context OS and Task State are Gate 6 | Facts table + self-hosted Mem0 baseline. Digest and Task State are not built |
| Browser | Playwright (headless chromium) behind the `browser` extra | Browser fill with an allowlisted transport and a human-gated submit |
| Runtime | Local / single-host Docker | Sandbox service (compose) |
| CI | GitHub Actions | ruff + pytest on 3.11/3.12, advisory pip-audit |
| License | MIT | [LICENSE](LICENSE) |

Dependencies are added only when a roadmap item needs them. The current runtime dependencies are pydantic-settings, LangGraph, the LangChain OpenAI integration, APScheduler, mem0ai (self-hosted semantic memory), and icalendar (ICS parsing; brings python-dateutil and tzdata).

## How to run

The agent runs: a chat loop over the LangGraph graph with tools, the approval gate for side effects, and audit + checkpoint storage.

```bash
python -m venv .venv
source .venv/bin/activate        # macOS/Linux; Windows Git Bash: source .venv/Scripts/activate
pip install -e ".[dev,browser]"
playwright install chromium      # only needed for browser fill; skip otherwise
cp .env.example .env             # then edit .env and set LOOM_ENV=dev
```

With the virtualenv active:

```bash
loom version    # prints the package version, needs no config or .env
loom doctor     # one line per check; exits 1 while a critical check fails
pytest          # runs the test suite
```

`loom doctor` fails on a missing `.env`, a broken config (the message names the variable, e.g. `LOOM_ENV`), or a data directory it cannot create or write. A missing `LLM_API_KEY` is a warning; `loom chat` needs it. A configured `LOOM_ICAL_URL` or `LOOM_IMAP_HOST` gets its own check line (unreachable = FAIL); when unset, the line is skipped entirely.

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

### Calendar and email drafts

Calendar tools are read-only and mail tools are draft-only. There is no send path: the codebase has no send tool, no SMTP, and the gate tests (`pytest tests/test_gate3_guards.py`) fail if a send or calendar-write symbol ever appears in `src/loom`. 日历只读、邮件只草稿：代码库中没有发送路径，门禁测试保证 send / 日历写入符号不会出现。

Configuration (all in `.env`; a variable left empty disables that feature):

| Variable | Meaning | Default |
| --- | --- | --- |
| `LOOM_ICAL_URL` | Read-only ICS feed URL; empty turns the calendar tools off | empty |
| `LOOM_IMAP_HOST` | IMAP server; empty turns the mail tools off | empty |
| `LOOM_IMAP_PORT` | IMAPS port | `993` |
| `LOOM_MAIL_USER` | Mailbox login, also used as the draft's From address | empty |
| `LOOM_MAIL_PASSWORD` | App password (never your login password) | empty |
| `LOOM_MAIL_FOLDER` | Folder drafts are appended to | `Drafts` |
| `LOOM_TIMEZONE` | IANA timezone for calendar windows and summaries | `Asia/Hong_Kong` |

**Calendar (read-only).** Paste the calendar's ICS feed URL as `LOOM_ICAL_URL`. Google Calendar: calendar Settings → "Integrate calendar" → copy the **secret address in iCal format**. Outlook.com: calendar → Share → publish, then copy the ICS link. Google 日历：设置 → 集成日历 → 复制 "iCal 格式的 secret 地址"；Outlook：共享 → 发布日历 → 复制 ICS 链接。The feed is fetched read-only and summarized in `LOOM_TIMEZONE`. The URL must be **https** (plain http is accepted only for `127.0.0.1`/`localhost`, which local demos use); responses are capped at 1 MiB and redirects that leave https are refused — these feed URLs carry a private token, so it is never sent over the network in the clear nor echoed in error messages. 订阅地址必须是 https（仅 127.0.0.1/localhost 允许 http，供本地演示），响应上限 1 MiB，离开 https 的重定向会被拒绝；出错信息只显示协议与主机，不回显含令牌的原始地址。

**Mail drafts (IMAP).** Use an app password, not your login password. Gmail: enable 2-Step Verification, then create an app password (Google Account → Security → App passwords) and set `LOOM_IMAP_HOST=imap.gmail.com`. A draft is appended with the `\Draft` flag to `LOOM_MAIL_FOLDER` (`[Gmail]/Drafts` on Gmail); the From address is `LOOM_MAIL_USER`. `LOOM_MAIL_FOLDER` must name a drafts folder: `INBOX` is rejected outright, and so are folders whose last segment is `Sent`, `Trash`, `Junk`, or `Deleted` — config validation fails with a message naming `LOOM_MAIL_FOLDER`. Draft replacement is UID-only: the old draft is found by UID search, confirmed by fetching its header (Message-ID must match exactly and the flags must carry `\Draft`), and only then replaced; without server-side UIDPLUS the old draft stays on the server and the result says so. Gmail 需要 2FA + 应用专用密码，主机填 `imap.gmail.com`，草稿夹用 `[Gmail]/Drafts`；`LOOM_MAIL_FOLDER` 必须是草稿夹（`INBOX` 直接拒绝，末段为 Sent/Trash/Junk/Deleted 的也会被拒绝），替换草稿只按 UID 精确操作，服务器不支持 UIDPLUS 时旧草稿保留并如实提示。

The tools and the gate:

| Tool | Side effect |
| --- | --- |
| `calendar_list_events` (today / tomorrow / week), `calendar_summarize_range` (ISO dates) | no — fetches and renders only |
| `browser_open` (url), `browser_fill` (fields) | no — prepares the page, submits nothing |
| `mail_create_draft(to, subject, body)`, `mail_update_draft(draft_id, to, subject, body)` | yes — pauses for approval |
| `browser_submit` | yes — pauses for approval |

A draft pauses with its **full recipient, subject, and body** in the approval prompt; `reject` never touches the IMAP server, and `approve` appends or replaces the draft — it is never sent. Credentials are read from Settings by the transports themselves; they never appear in tool arguments, audit records, or logs (an audit record caps each string argument value at 200 characters). 草稿审批会展示完整收件人、主题与正文；reject 时不会调用 IMAP 服务器，approve 后只落草稿、绝不发送；凭证只从配置读取，不进工具参数、审计或日志。

**Browser fill (Playwright).** Install the extra first: `pip install -e ".[browser]"`. `browser_open` navigates (GET only) and `browser_fill` types into the open page; both run without approval because they submit nothing. `browser_submit` is the only click in the codebase and pauses for approval — the prompt shows the page's real url and every field value read back from the browser, merged over anything the model claims. A request firewall passes every request the page makes through the host allowlist — loopback plus `LOOM_BROWSER_ALLOWED_HOSTS` (empty means loopback only) — and aborts the rest before it is sent; a submission the firewall blocks raises instead of reading as accepted, and a navigation that lands off the allowlist closes the page. Password fields are masked to `***` everywhere the model looks. 浏览器代填先装 `pip install -e ".[browser]"`；打开和填写不需要批准（不提交任何内容），`browser_submit` 是代码里唯一的点击、必须人工批准，批准框展示浏览器里真实的 URL 与各字段值（以浏览器为准，盖过模型转述）。页面发出的每个请求都过域名防火墙（回环加 `LOOM_BROWSER_ALLOWED_HOSTS`，留空只允许回环），未过白名单的请求在发出前被拦截；被拦的提交会报错而不是算作成功，落到白名单外的页面会被关闭。密码字段在模型可见的一切输出里都显示为 `***`。

A no-key demo of the whole path (local HTTP ICS feed, scripted model, fake mail transport):

```bash
python scripts/demo_gate3_calendar_mail.py
```

A no-key demo of the browser path (local form fixture, real headless chromium, scripted model):

```bash
python scripts/demo_gate4_browser.py
```

Example prompts / 示例提示词：

```text
What's on tomorrow?          /  总结明天日程
Draft a mail to peer@x about the plan  /  给 peer@x 写一封关于计划的邮件草稿
```

### Inbound, Request Scope, Gateway and Console (Gate 7)

Untrusted inbound (simulated mail until Gate 8) is accepted by a seam that creates a triage task and issues the minimal Request Scope: an authenticated owner message gets a draft-only scope (`memory_write` false, approval required); an unauthenticated one gets no scope at all, so the gateway refuses every tool call for it. The Tool Gateway checks every tool call outside the model, before it runs, against that scope and fails closed - including when the audit write itself fails. A scope cannot be minted or widened by the model (`issue_scope` is control-plane only; `send_permission` is always false and a scope constructed with it true raises). The local draft path writes a `local_only` record and nothing else: no send tool, no send route, no SMTP. Every Gate 7 audit row threads the scope's `correlation_id`; `loom console` serves the four-view skin (Inbox / Draft / Approval / Audit) on 127.0.0.1 only, with a session, per-session CSRF tokens, and Origin/Host checks - an approval needs the session, not a hidden button.

不可信入站在 Gate 7 用模拟邮件（fixtures/inbound/）。入站 seam 只做三件事：收件入箱、建任务、发行最小 Scope——已认证的主人邮件拿到「仅本地草稿」权限（memory_write=false、必须审批），未认证的什么都不拿，网关对它的每次工具调用都拒绝。Tool Gateway 在模型外、执行前校验每次工具调用，fail closed（连审计写失败也拒绝执行）。模型不能自授或扩权：`issue_scope` 只在控制面，`send_permission` 恒为 false。本地草稿只写一条 `local_only` 记录，没有发送工具、没有发送路由、没有 SMTP。Gate 7 起审计新行带 `correlation_id` 串起整条链。`loom console` 只监听 127.0.0.1，四视图（Inbox / Draft / Approval / Audit），session + CSRF + Origin/Host 校验，审批必须有 session。安装控制台依赖：`pip install -e ".[console]"`。

A keyless demo of the closed loop (accept -> scoped triage -> approval -> one local draft, plus the four refusals):

```bash
python scripts/demo_gate7_inbound.py
```

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

### Daily operation

The every-day loop, in one place:

```bash
loom chat                        # the REPL; side-effect tools pause for approve/reject
loom worker                      # keep routines firing on their cron schedules
loom approvals list              # resolve anything the gate held while you were away
loom memory facts                # what Loom firmly knows; `set`/`del` to author it
loom memory digest|episodic|feedback|compact   # the working set: fold recent activity into the digest
loom tasks list|show|recover     # the task state machine; recover after a restart
loom console                     # the local operator console on 127.0.0.1 (needs the console extra)
loom stats                       # budget, token spend, approval and failure counters
loom eval                        # regression confidence: cases must stay green, the canary must stay red
```

Set a spending brake in `.env` before going hands-off: `LOOM_DAILY_BUDGET_TOKENS=200000` refuses new chat turns and routine runs once the day's estimate reaches the line (0 = unlimited), and the refusal lands in the audit log with `decision=rejected`. `LOOM_TOKEN_PRICE_USD_PER_1K` is optional and only turns the `loom stats` token count into a dollars estimate.

预算与日常运维：`LOOM_DAILY_BUDGET_TOKENS` 是每日 token 闸（0 为不限），到达后新的 chat 回合与例程运行都会被拒绝并写入审计（`decision=rejected`）；`loom stats` 汇总预算、用量与审计计数；`loom eval` 跑回归评测，用例必须保持绿、canary 必须保持红。

### Demos

Seven keyless demos (scripted models, local fixtures) each prove one path end to end:

```bash
python scripts/demo_gate1.py                # core loop: harmless tools answer, side effects gate
python scripts/demo_gate2.py                # sandbox tidy with approved writes
python scripts/demo_gate2_routines.py       # routine triggers and the pending approval queue
python scripts/demo_gate3_memory.py         # facts, the memory note, and the explicit write-back
python scripts/demo_gate3_calendar_mail.py  # calendar summaries and draft gating
python scripts/demo_gate4_browser.py        # browser fill behind the human gate
python scripts/demo_gate5.py                # budget brake, backup/restore, eval summary
```
python scripts/demo_gate6_tasks.py          # task state machine and the Context OS (Gate 6)
python scripts/demo_gate7_inbound.py        # simulated inbound loop: scope, gateway, local draft (Gate 7)
```

### Backup and restore

`loom backup [destination]` zips the five data-dir roots — `facts.db`, `routines.json`, `approvals/`, `audit/`, `checkpoints/` — into `loom-backup-<timestamp>.zip` (destination defaults to `./backups`). Nothing else is included: `.env`, spend ledgers, and the semantic-memory store never enter an archive, so a backup cannot become a secrets exfiltration path. Run it while Loom is idle; `facts.db` is captured as the file stands.

`loom restore <archive> [--force]` writes the archive's members back into the data dir. It refuses when a target file already exists unless you pass `--force`, and it refuses any archive member that would land outside the data dir. The drill:

```bash
loom backup ./backups                                     # 1. archive the current state
rm -rf "$LOOM_DATA_DIR"/{facts.db,routines.json,approvals,audit,checkpoints}
loom restore ./backups/loom-backup-<stamp>.zip             # 2. restore after a wipe
loom memory facts                                          # 3. the facts are back
```

备份与恢复：`loom backup` 只打包五个数据根（facts.db、routines.json、approvals/、audit/、checkpoints/），`.env`、spend、语义记忆库绝不进包，备份不会成为密钥外带路径；`loom restore` 恢复时目标文件已存在会拒绝（除非 `--force`），并拒绝任何会写到数据目录之外的包成员。请在 Loom 空闲时备份。

## Docs

- [docs/README.md](docs/README.md) — which file owns progress, the plan, and decisions
- [docs/VISION.md](docs/VISION.md) — vision and boundaries
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — target layers, with Gate 6+ marked unbuilt
- [docs/DECISIONS.md](docs/DECISIONS.md) — locked choices
- [docs/RESUME_STORY.md](docs/RESUME_STORY.md) — what this repository demonstrates, with reproducible numbers
- [docs/ROADMAP.md](docs/ROADMAP.md) — Gate 6+ plan (Gate 1–5 are done)
- [docs/STATUS.md](docs/STATUS.md) — what the repository actually contains
- [docs/SECURITY.md](docs/SECURITY.md) — secrets, drafts, approval
- [CONTRIBUTING.md](CONTRIBUTING.md) — status updates are part of shipping; decisions live in the repo, not on an external page

## License

[MIT](LICENSE). Copyright (c) 2026 NorthAdb.
