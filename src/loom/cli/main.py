"""Command line entry points: loom version, doctor, chat, routines, approvals, triage, memory, tasks, worker, stats, backup, restore, eval, mcp."""

import argparse
import imaplib
import json
import logging
import os
import shlex
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Sequence
from contextlib import suppress
from functools import partial
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from langchain_core.messages import HumanMessage
from langgraph.types import Command

import loom
from loom.audit.log import AuditLog
from loom.core.config import ConfigError, Settings, get_settings
from loom.core.logging import set_run_id, setup_logging
from loom.core.runner import resolve_pending, run_routine, run_triage
from loom.core.runtime import open_graph
from loom.gateway.pending import PENDING, PendingApproval, PendingStore
from loom.memory import (
    DigestStore,
    EpisodicStore,
    FactsStore,
    FeedbackStore,
    build_semantic_memory,
    compact,
)
from loom.ops.backup import create_backup, restore_backup
from loom.ops.budget import UNLIMITED_LINE, BudgetGuard, SpendLedger, guard_from_settings, turn_tokens
from loom.ops.eval import format_result, format_summary, load_cases, run_case, run_cases
from loom.ops.routines import Routine, RoutineStore
from loom.tasks import TaskStatus, TaskStore
from loom.tools.calendar import HttpCalendarTransport, redact_url
from loom.tools.mcp import McpError, McpServerStore, register_server, review_server
from loom.tools.registry import build_mail_transport

logger = logging.getLogger("loom")

_MIN_PYTHON = (3, 11)

# Relative to the working directory, so `loom eval` runs from the repository
# root; a module constant so tests can point it at a temporary cases dir.
EVAL_CASES_DIR = Path("tests/eval/cases")

_OK = "ok     "
_MISSING = "MISSING"
_FAIL = "FAIL   "
_WARN = "warn   "


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".loom-write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


def _sandbox_reachable(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/health", timeout=2) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def _calendar_reachable(url: str) -> bool:
    """One GET of the ICS feed; any failure means unreachable. Never raises."""
    try:
        HttpCalendarTransport(url, timeout=2).fetch_ics()
    except Exception:  # noqa: BLE001 - doctor prints a FAIL line, never a traceback
        return False
    return True


def _imap_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _imap_drafts_special_use(host: str, port: int, user: str, password: str, folder: str) -> bool | None:
    """Whether the server lists `folder` with the \\Drafts special-use flag.

    True when it does, False when it is listed without it, None when the
    check cannot run (connect, login, or LIST failure, folder absent).
    Doctor only warns on False, so this stays advisory and never raises.
    """
    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=2)
    except Exception:  # noqa: BLE001 - advisory check must never break doctor
        return None
    try:
        client.login(user, password)
        status, data = client.list()
    except Exception:  # noqa: BLE001 - advisory check must never break doctor
        return None
    finally:
        with suppress(Exception):
            client.logout()
    if status != "OK":
        return None
    for line in data:
        text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line or "")
        if f'"{folder}"' not in text:
            continue
        return r"\drafts" in text.lower()
    return None


def _doctor() -> int:
    failed = False

    if sys.version_info >= _MIN_PYTHON:
        print(f"{_OK}python {sys.version.split()[0]}")
    else:
        print(f"{_FAIL}python {sys.version.split()[0]} (need >= 3.11)")
        failed = True

    if Path(".env").is_file():
        print(f"{_OK}.env")
    else:
        print(f"{_MISSING} .env (copy .env.example to .env and set LOOM_ENV)")
        failed = True

    settings: Settings | None = None
    try:
        settings = get_settings()
        print(f"{_OK}config")
    except ConfigError as exc:
        print(f"{_FAIL}config: {exc}")
        failed = True

    data_dir = Path(settings.loom_data_dir if settings else os.environ.get("LOOM_DATA_DIR", "./data"))
    if _writable(data_dir):
        print(f"{_OK}data dir {data_dir}")
    else:
        print(f"{_FAIL}data dir {data_dir} (cannot create or write)")
        failed = True

    for name, probe in (
        ("tasks store", lambda: len(TaskStore(data_dir).list())),
        ("context store", lambda: len(DigestStore(data_dir).get()) + EpisodicStore(data_dir).count_unfolded()),
    ):
        try:
            probe()
            print(f"{_OK}{name}")
        except (OSError, sqlite3.Error) as exc:
            print(f"{_FAIL}{name} ({exc})")
            failed = True

    try:
        import fastapi  # noqa: F401

        print(f"{_OK}console extra")
    except ImportError:
        print(f"{_WARN}console extra not installed (`loom console` needs it: pip install -e \".[console]\")")

    sandbox_url = settings.loom_sandbox_url if settings else os.environ.get("LOOM_SANDBOX_URL", "")
    if sandbox_url:
        if _sandbox_reachable(sandbox_url):
            print(f"{_OK}sandbox service {sandbox_url}")
        else:
            print(f"{_FAIL}sandbox service {sandbox_url} (no /health response; try: docker compose up -d --build)")
            failed = True
    else:
        sandbox_root = Path(settings.loom_sandbox_root if settings else os.environ.get("LOOM_SANDBOX_ROOT", "./sandbox"))
        if _writable(sandbox_root):
            print(f"{_OK}sandbox root {sandbox_root}")
        else:
            print(f"{_FAIL}sandbox root {sandbox_root} (cannot create or write)")
            failed = True

    api_key = settings.llm_api_key if settings else os.environ.get("LLM_API_KEY", "")
    if api_key:
        print(f"{_OK}LLM_API_KEY present")
    else:
        print(f"{_WARN}LLM_API_KEY not set (loom chat needs it)")

    ical_url = settings.ical_url if settings else os.environ.get("LOOM_ICAL_URL", "")
    if ical_url:
        shown = redact_url(ical_url)
        if _calendar_reachable(ical_url):
            print(f"{_OK}calendar feed {shown}")
        else:
            print(f"{_FAIL}calendar feed {shown} (no ICS response; check LOOM_ICAL_URL)")
            failed = True

    imap_host = settings.imap_host if settings else os.environ.get("LOOM_IMAP_HOST", "")
    if imap_host:
        try:
            imap_port = settings.imap_port if settings else int(os.environ.get("LOOM_IMAP_PORT") or "993")
        except ValueError:
            imap_port = -1
        folder = settings.mail_folder if settings else os.environ.get("LOOM_MAIL_FOLDER", "") or "Drafts"
        if _imap_reachable(imap_host, imap_port):
            print(f"{_OK}imap {imap_host}:{imap_port} drafts folder {folder}")
            mail_user = settings.mail_user if settings else os.environ.get("LOOM_MAIL_USER", "")
            mail_password = settings.mail_password if settings else os.environ.get("LOOM_MAIL_PASSWORD", "")
            if mail_user and mail_password and _imap_drafts_special_use(imap_host, imap_port, mail_user, mail_password, folder) is False:
                print(
                    f"{_WARN}imap drafts folder {folder} does not advertise the \\Drafts special-use flag; "
                    "verify it is a drafts folder"
                )
        else:
            print(
                f"{_FAIL}imap {imap_host}:{imap_port} drafts folder {folder} "
                "(no TCP connection; check LOOM_IMAP_HOST and LOOM_IMAP_PORT)"
            )
            failed = True

    return 1 if failed else 0


def chat_session(graph, thread_id: str, read: Callable[[str], str], emit: Callable[[str], None], *, budget: "BudgetGuard | None" = None) -> None:
    """Drive one REPL over a compiled graph: turn, gate approval loop, answer.

    With a budget guard, a turn the guard refuses is never invoked, and a
    completed turn records its token spend.
    """
    config = {"configurable": {"thread_id": thread_id}}
    while True:
        text = read("you> ").strip()
        if not text:
            continue
        if text.lower() in ("quit", "exit"):
            return

        run_id = uuid.uuid4().hex
        set_run_id(run_id)
        if budget is not None and not budget.allow("chat", run_id):
            emit(budget.refusal_line())
            continue
        logger.info("chat turn", extra={"thread_id": thread_id})
        result = graph.invoke({"messages": [HumanMessage(content=text)], "run_id": run_id}, config)
        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            for item in payload["tools"]:
                emit(f"approval needed: {item['name']} args={item['args']}")
            emit(payload["reason"])
            while True:
                answer = read("approve or reject? ").strip().lower()
                if answer in ("approve", "reject"):
                    break
                emit("please answer approve or reject")
            result = graph.invoke(Command(resume={"decision": answer}), config)
        emit(result["messages"][-1].content)
        if budget is not None:
            budget.record("chat", run_id, turn_tokens(result["messages"]))


def _chat(thread_id: str | None, read: Callable[[str], str] = input, emit: Callable[[str], None] = print, *, mcp: bool = False) -> int:
    settings = get_settings()
    setup_logging(settings)
    with open_graph(settings, mcp=mcp) as graph:
        session_id = thread_id or uuid.uuid4().hex
        emit(f"thread {session_id}")
        emit("type a message; side-effect tools pause for approve/reject; 'quit' exits")
        try:
            chat_session(graph, session_id, read, emit, budget=guard_from_settings(settings, settings.loom_data_dir))
        except (EOFError, KeyboardInterrupt):
            pass
    emit("goodbye")
    return 0


def _routine_line(routine: Routine) -> str:
    return (
        f"{routine.id}  enabled={str(routine.enabled).lower()}  cron='{routine.cron}'"
        f"  last_status={routine.last_status or '-'}  last_run_at={routine.last_run_at or '-'}"
    )


def _routines_add(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = RoutineStore(settings.loom_data_dir)
    routine = Routine(id=args.id, name=args.name, cron=args.cron, prompt=args.prompt)
    try:
        stored = store.add(routine)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(f"added routine {stored.id}: cron='{stored.cron}' enabled={str(stored.enabled).lower()}")
    return 0


def _routines_list(args: argparse.Namespace) -> int:
    settings = get_settings()
    routines = RoutineStore(settings.loom_data_dir).load()
    if not routines:
        print("(none)")
        return 0
    for routine in routines:
        print(_routine_line(routine))
    return 0


def _routines_remove(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        removed = RoutineStore(settings.loom_data_dir).remove(args.id)
    except KeyError as exc:
        print(f"error: {exc.args[0]}")
        return 1
    print(f"removed routine {removed.id}")
    return 0


def _routines_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(settings)
    store = RoutineStore(settings.loom_data_dir)
    pending = PendingStore(settings.loom_data_dir)
    try:
        routine = store.get(args.id)
    except KeyError as exc:
        print(f"error: {exc.args[0]}")
        return 1
    try:
        outcome = run_routine(routine, settings, store, pending)
    except (ConfigError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    if outcome.outcome == "refused":
        print(outcome.answer)
        return 1
    print(f"thread {outcome.thread_id}")
    if outcome.outcome == PENDING:
        print(f"waiting for approval: {outcome.pending_id}")
        print(f"resolve with: loom approvals approve {outcome.pending_id}  or  loom approvals reject {outcome.pending_id}")
    else:
        print(outcome.answer)
        print("completed")
    return 0


def _triage(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(settings)
    pending = PendingStore(settings.loom_data_dir)
    try:
        outcome = run_triage(args.message_id, settings, pending)
    except (ConfigError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    if outcome.outcome == "refused":
        print(outcome.answer)
        return 1
    print(f"thread {outcome.thread_id}")
    if outcome.outcome == PENDING:
        print(f"waiting for approval: {outcome.pending_id}")
        print(f"resolve with: loom approvals approve {outcome.pending_id}  or  loom approvals reject {outcome.pending_id}")
    else:
        print(outcome.answer)
        print("completed")
    return 0


def _print_pending_payload(record: "PendingApproval") -> None:
    """Show every tool's full real args; the human decides on what is printed here."""
    for item in record.tools:
        print(f"  tool={item['name']}  args={json.dumps(item.get('args', {}), ensure_ascii=False)}")


def _approvals_list(args: argparse.Namespace) -> int:
    settings = get_settings()
    waiting = PendingStore(settings.loom_data_dir).list_pending()
    if not waiting:
        print("(none)")
        return 0
    for record in waiting:
        print(f"{record.pending_id}  routine={record.routine_id}  created={record.created_at}")
        _print_pending_payload(record)
    return 0


def _approvals_decide(args: argparse.Namespace, decision: str) -> int:
    settings = get_settings()
    setup_logging(settings)
    pending = PendingStore(settings.loom_data_dir)
    try:
        record = pending.get(args.pending_id)
    except KeyError as exc:
        print(f"error: {exc.args[0]}")
        return 1
    if record.status == PENDING:
        print(f"resolving {record.pending_id} (routine={record.routine_id}) with {decision}:")
        _print_pending_payload(record)
    try:
        outcome = resolve_pending(args.pending_id, decision, settings, pending, mail_transport=build_mail_transport(settings))
    except (ConfigError, KeyError, RuntimeError, ValueError) as exc:
        print(f"error: {exc.args[0] if isinstance(exc, KeyError) else exc}")
        return 1
    if outcome.outcome == PENDING:
        print("still pending: the run asked for approval of further side effects")
        print(f"resolve with: loom approvals approve {args.pending_id}  or  loom approvals reject {args.pending_id}")
    else:
        print(outcome.answer)
    return 0


def _fact_line(fact) -> str:
    return f"- {fact.key}: {fact.value} ({fact.source}, {fact.updated_at})"


def _memory_facts_list(args: argparse.Namespace) -> int:
    settings = get_settings()
    facts = FactsStore(settings.loom_data_dir).list()
    if not facts:
        print("(no facts)")
        return 0
    for fact in facts:
        print(_fact_line(fact))
    return 0


def _memory_facts_set(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        stored = FactsStore(settings.loom_data_dir).upsert(args.key, " ".join(args.value))
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(_fact_line(stored))
    return 0


def _memory_facts_del(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        removed = FactsStore(settings.loom_data_dir).delete(args.key)
    except KeyError as exc:
        print(f"error: {exc.args[0]}")
        return 1
    print(f"removed fact {removed.key}")
    return 0


def _memory_search(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        semantic = build_semantic_memory(settings)
        hits = semantic.search(" ".join(args.query), limit=settings.memory_inject_limit)
    except (ConfigError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 1
    if not hits:
        print("(no memories)")
        return 0
    for hit in hits:
        print(f"- {hit}")
    return 0


def _task_line(task) -> str:
    line = f"{task.task_id}  status={task.status.value}  kind={task.kind}  {task.title}"
    if task.status is TaskStatus.WAITING_APPROVAL and task.waiting_reason:
        line += f"  waiting: {task.waiting_reason}"
    return line


def _resolve_task(store: TaskStore, ref: str):
    """One task by exact id, or by unique id prefix; None with a message printed otherwise."""
    if not ref:
        print("error: give a task id (or a unique prefix of one)")
        return None
    if len(ref) == 32:
        try:
            return store.get(ref)
        except KeyError:
            print(f"error: no such task: {ref!r}")
            return None
    matches = [task for task in store.list() if task.task_id.startswith(ref)]
    if not matches:
        print(f"error: no task matches {ref!r}")
        return None
    if len(matches) > 1:
        print(f"error: {len(matches)} tasks match {ref!r}; use a longer prefix")
        return None
    return matches[0]


def _tasks_list(args: argparse.Namespace) -> int:
    settings = get_settings()
    status = TaskStatus(args.status) if args.status else None
    tasks = TaskStore(settings.loom_data_dir).list(status=status)
    if not tasks:
        print("(no tasks)")
        return 0
    for task in tasks:
        print(_task_line(task))
    return 0


def _tasks_show(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = TaskStore(settings.loom_data_dir)
    task = _resolve_task(store, args.id)
    if task is None:
        return 1
    print(_task_line(task))
    if task.error:
        print(f"error: {task.error}")
    if task.result:
        print(f"result: {task.result}")
    for event in store.history(task.task_id):
        reason = f"  ({event.reason})" if event.reason else ""
        print(f"  {event.ts}  {event.from_status or '-'} -> {event.to_status}{reason}")
    return 0


def _tasks_recover(args: argparse.Namespace) -> int:
    settings = get_settings()
    recovered = TaskStore(settings.loom_data_dir).recover()
    if not recovered:
        print("(nothing to recover)")
        return 0
    for task in recovered:
        print(f"recovered: {task.task_id} is now blocked (was running when the process stopped)")
    return 0


def _memory_digest(args: argparse.Namespace) -> int:
    settings = get_settings()
    digest = DigestStore(settings.loom_data_dir).get()
    print(digest if digest else "(no digest yet; run `loom memory compact` after turns accrue)")
    return 0


def _memory_episodic(args: argparse.Namespace) -> int:
    settings = get_settings()
    entries = EpisodicStore(settings.loom_data_dir).recent(args.limit, unfolded_only=not args.all)
    if not entries:
        print("(no episodic entries)")
        return 0
    for entry in entries:
        marker = " folded" if entry.folded else ""
        print(f"{entry.seq}  {entry.ts}  {entry.kind}: {entry.text[:120]}{marker}")
    return 0


def _memory_feedback_add(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        entry = FeedbackStore(settings.loom_data_dir).add("owner", " ".join(args.text))
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(f"{entry.seq}  {entry.ts}  {entry.source}: {entry.text}")
    return 0


def _memory_feedback_list(args: argparse.Namespace) -> int:
    settings = get_settings()
    entries = FeedbackStore(settings.loom_data_dir).recent(20)
    if not entries:
        print("(no feedback)")
        return 0
    for entry in entries:
        print(f"{entry.seq}  {entry.ts}  {entry.source}: {entry.text}")
    return 0


def _memory_compact(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        result = compact(settings.loom_data_dir)
    except (OSError, sqlite3.Error) as exc:
        print(f"error: compact failed, the old digest is unchanged ({exc})")
        return 1
    if result.folded == 0:
        print("(nothing to compact)")
        return 0
    print(f"folded {result.folded} entr{'y' if result.folded == 1 else 'ies'} into the digest")
    print(f"digest: {len(result.old_digest)} -> {len(result.new_digest)} chars")
    return 0


def _console(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        from loom.console.app import create_app
    except ModuleNotFoundError:
        print("error: the console extra is not installed; run: pip install -e \".[console]\"")
        return 1
    import uvicorn

    app = create_app(settings.loom_data_dir, settings, port=args.port)
    print(f"operator console on http://127.0.0.1:{args.port} (local machine only; Ctrl-C stops)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _worker(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(settings)
    store = RoutineStore(settings.loom_data_dir)
    pending = PendingStore(settings.loom_data_dir)
    routines = [routine for routine in store.load() if routine.enabled]
    scheduler = BackgroundScheduler()
    for routine in routines:
        scheduler.add_job(
            run_routine,
            trigger=CronTrigger.from_crontab(routine.cron),
            args=[routine, settings, store, pending],
            id=routine.id,
        )
    scheduler.start()
    print(f"worker started: {len(routines)} routine(s) scheduled; Ctrl-C stops")
    print("routines added while the worker runs are not hot-reloaded; restart the worker to load them")
    try:
        while True:
            time.sleep(1)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        scheduler.shutdown(wait=False)
    print("worker stopped")
    return 0


def _runs_line(counts: dict[str, int]) -> str:
    return f"chat={counts.get('chat', 0)}, routine={counts.get('routine', 0)}"


def _count_audit_decisions(data_dir: Path) -> dict[str, int]:
    """Decisions counted across <data dir>/audit/*.jsonl; unreadable files and lines are skipped."""
    counts = {"approved": 0, "rejected": 0, "failed": 0}
    for path in sorted((data_dir / "audit").glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            decision = record.get("decision") if isinstance(record, dict) else None
            if decision in counts:
                counts[decision] += 1
    return counts


def _stats(args: argparse.Namespace) -> int:
    settings = get_settings()
    data_dir = settings.loom_data_dir
    ledger = SpendLedger(data_dir)
    guard = guard_from_settings(settings, data_dir)
    print(guard.limit_line() if guard else UNLIMITED_LINE)
    price = settings.token_price_usd_per_1k
    today = ledger.today_tokens()
    estimate = f", ${today / 1000 * price:.2f} estimated" if price > 0 else ""
    print(f"today: {today} tokens{estimate}, runs: {_runs_line(ledger.runs_today())}")
    print(f"total: {ledger.total_tokens()} tokens, runs: {_runs_line(ledger.runs_total())}")
    decisions = _count_audit_decisions(data_dir)
    print(
        f"audit: approvals approved={decisions['approved']}, rejected={decisions['rejected']}, "
        f"tool executions failed={decisions['failed']}"
    )
    return 0


def _backup(args: argparse.Namespace) -> int:
    settings = get_settings()
    archive = create_backup(settings.loom_data_dir, Path(args.destination) if args.destination else Path("backups"))
    with zipfile.ZipFile(archive) as zf:
        count = len(zf.namelist())
    print(f"backup written: {archive} ({count} files)")
    return 0


def _restore(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        restored = restore_backup(Path(args.archive), settings.loom_data_dir, overwrite=args.force)
    except (ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}")
        return 1
    for path in restored:
        print(path)
    return 0


def _eval_one_case(name: str) -> int:
    try:
        cases = load_cases(EVAL_CASES_DIR)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    wanted = [case for case in cases if case.name == name]
    if not wanted:
        print(f"error: no eval case named {name!r} under {EVAL_CASES_DIR}")
        return 1
    result = run_case(wanted[0], EVAL_CASES_DIR)
    print(format_result(result))
    return 0 if result.met_expectation else 1


def _eval(args: argparse.Namespace) -> int:
    if not EVAL_CASES_DIR.is_dir():
        print(f"eval cases not found at {EVAL_CASES_DIR}; run from the repository root")
        return 1
    if args.name:
        return _eval_one_case(args.name)
    try:
        summary = run_cases(EVAL_CASES_DIR)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(format_summary(summary))
    return 0 if summary.rate >= 0.8 and summary.canary_ok else 1


def _parse_mcp_command(text: str) -> "list[str]":
    """A JSON array of argv strings, or a shell-ish command line split the simple way."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return shlex.split(text)
    if not isinstance(parsed, list) or not all(isinstance(part, str) for part in parsed):
        raise McpError("--command must be a JSON array of strings or a shell-ish command line")
    return parsed


def _mcp_register(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = McpServerStore(settings.loom_data_dir)
    try:
        command = _parse_mcp_command(args.mcp_command_line)
        allowed = [name.strip() for name in args.allow.split(",") if name.strip()]
        if not allowed:
            raise McpError("--allow must name at least one tool, comma-separated")
        duplicate = any(record.server_id == args.id for record in store.list())
        record = register_server(
            store,
            server_id=args.id,
            name=args.name,
            command=command,
            allowed_tools=allowed,
            credential_env=args.credential_env,
            force=args.force,
        )
    except (McpError, OSError) as exc:
        print(f"error: {exc}")
        return 1
    AuditLog(settings.loom_data_dir).append(
        run_id="",
        actor="mcp-cli",
        tool="mcp.register",
        args={"server_id": record.server_id, "fingerprint": record.schema_fingerprint[:12], "force": args.force},
        decision="reregistered" if duplicate else "registered",
    )
    print(f"registered mcp server {record.server_id}: fingerprint {record.schema_fingerprint[:12]}")
    print(f"allowed tools: {', '.join(sorted(record.allowed_tools))}")
    if record.credential_env:
        print(f"credential: env var {record.credential_env} (the value is never stored)")
    if record.needs_review:
        print(f"frozen pending review; run `loom mcp review {record.server_id}` before its tools resolve")
    return 0


def _mcp_list(args: argparse.Namespace) -> int:
    records = McpServerStore(get_settings().loom_data_dir).list()
    if not records:
        print("(none)")
        return 0
    for record in records:
        print(
            f"{record.server_id}  name={record.name}  enabled={str(record.enabled).lower()}"
            f"  needs_review={str(record.needs_review).lower()}  fingerprint={record.schema_fingerprint[:12]}"
            f"  allowed={','.join(sorted(record.allowed_tools))}"
            f"  credential_env={record.credential_env or '-'}"
        )
    return 0


def _mcp_revoke(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = McpServerStore(settings.loom_data_dir)
    try:
        record = store.set_status(args.id, enabled=False)
    except KeyError as exc:
        print(f"error: {exc.args[0]}")
        return 1
    AuditLog(settings.loom_data_dir).append(
        run_id="",
        actor="mcp-cli",
        tool="mcp.revoke",
        args={"server_id": record.server_id},
        decision="revoked",
    )
    print(f"revoked {record.server_id}: enabled={str(record.enabled).lower()}")
    return 0


def _mcp_review(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = McpServerStore(settings.loom_data_dir)
    try:
        record, diff = review_server(store, args.id)
    except KeyError as exc:
        print(f"error: {exc.args[0]}")
        return 1
    except (McpError, OSError) as exc:
        print(f"error: {exc}")
        return 1
    AuditLog(settings.loom_data_dir).append(
        run_id="",
        actor="mcp-cli",
        tool="mcp.review",
        args={
            "server_id": record.server_id,
            "old_fingerprint": diff["old_fingerprint"][:12],
            "new_fingerprint": diff["new_fingerprint"][:12],
            "added": diff["added"],
            "removed": diff["removed"],
        },
        decision="reviewed",
    )
    print(f"reviewed {record.server_id}: needs_review={str(record.needs_review).lower()}")
    print(f"  old fingerprint: {diff['old_fingerprint'][:12]}")
    print(f"  new fingerprint: {diff['new_fingerprint'][:12]}")
    print(f"  server now lists: {', '.join(diff['listed']) or '(none)'}")
    print(f"  added since registration: {', '.join(diff['added']) or '(none)'}")
    print(f"  removed since registration: {', '.join(diff['removed']) or '(none)'}")
    print("  (the allowed subset only changes through a --force re-registration)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loom", description="Loom personal agent CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="Print the package version.")
    subparsers.add_parser("doctor", help="Check the local environment.")
    chat_parser = subparsers.add_parser("chat", help="Chat with the agent.")
    chat_parser.add_argument("--thread-id", help="Resume an existing thread from its checkpoint.")
    chat_parser.add_argument(
        "--mcp",
        action="store_true",
        help="Expose registered MCP servers' tools to this chat (ADR-020); off by default.",
    )

    routines_parser = subparsers.add_parser("routines", help="Manage scheduled routines.")
    routines_sub = routines_parser.add_subparsers(dest="routines_command", required=True)
    add_parser = routines_sub.add_parser("add", help="Register a routine (cron is a 5-field crontab).")
    add_parser.add_argument("--id", required=True, help="Unique slug, e.g. daily_sandbox_stats.")
    add_parser.add_argument("--name", required=True, help="Human-readable name.")
    add_parser.add_argument("--cron", required=True, help="5-field crontab: 'M H DOM MON DOW'.")
    add_parser.add_argument("--prompt", required=True, help="Prompt sent to the graph on every fire.")
    routines_sub.add_parser("list", help="List routines.")
    remove_parser = routines_sub.add_parser("remove", help="Delete one routine by id.")
    remove_parser.add_argument("id")
    run_parser = routines_sub.add_parser("run", help="Trigger one routine run now, through the shared graph.")
    run_parser.add_argument("id")

    approvals_parser = subparsers.add_parser("approvals", help="Handle pending routine approvals.")
    approvals_sub = approvals_parser.add_subparsers(dest="approvals_command", required=True)
    approvals_sub.add_parser("list", help="List pending approvals.")
    approve_parser = approvals_sub.add_parser("approve", help="Approve a pending run and resume it.")
    approve_parser.add_argument("pending_id")
    reject_parser = approvals_sub.add_parser("reject", help="Reject a pending run.")
    reject_parser.add_argument("pending_id")

    triage_parser = subparsers.add_parser(
        "triage",
        help="Run one accepted inbox message through triage; a draft proposal waits for approval.",
    )
    triage_parser.add_argument("message_id", help="The inbox message id, e.g. sim-001 or imap-104.")

    subparsers.add_parser(
        "worker",
        help="Run enabled routines on their cron schedule; new routines are not hot-reloaded.",
    )

    memory_parser = subparsers.add_parser("memory", help="Inspect and manage long-term memory.")
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    facts_parser = memory_sub.add_parser("facts", help="List facts (the default action), set or del one.")
    facts_sub = facts_parser.add_subparsers(dest="facts_command")
    facts_set_parser = facts_sub.add_parser("set", help="Insert or correct one fact (same key = correction).")
    facts_set_parser.add_argument("key", help="Short stable name, e.g. reply_language.")
    facts_set_parser.add_argument("value", nargs="+", help="Fact value; secrets are refused.")
    facts_del_parser = facts_sub.add_parser("del", help="Delete one fact by key.")
    facts_del_parser.add_argument("key")
    memory_search_parser = memory_sub.add_parser("search", help="Search the semantic memory store.")
    memory_search_parser.add_argument("query", nargs="+")
    memory_sub.add_parser("digest", help="Show the working-set digest (compacted episodic activity).")
    episodic_parser = memory_sub.add_parser("episodic", help="List recent episodic entries, newest first.")
    episodic_parser.add_argument("--limit", type=int, default=10, help="How many entries to show (default 10).")
    episodic_parser.add_argument(
        "--all", action="store_true", help="Include entries already folded into the digest."
    )
    feedback_parser = memory_sub.add_parser("feedback", help="Record owner feedback, or list it.")
    feedback_sub = feedback_parser.add_subparsers(dest="feedback_command")
    feedback_add_parser = feedback_sub.add_parser("add", help="Record one correction for future turns.")
    feedback_add_parser.add_argument("text", nargs="+", help="The feedback; secrets are refused.")
    feedback_sub.add_parser("list", help="List recent feedback, newest first.")
    memory_sub.add_parser("compact", help="Fold the oldest episodic entries into the digest.")

    tasks_parser = subparsers.add_parser("tasks", help="Inspect and manage the task state machine.")
    tasks_sub = tasks_parser.add_subparsers(dest="tasks_command", required=True)
    tasks_list_parser = tasks_sub.add_parser("list", help="List tasks in creation order.")
    tasks_list_parser.add_argument(
        "--status", help="Filter to one status: created, running, waiting_approval, blocked, completed, failed, cancelled."
    )
    tasks_show_parser = tasks_sub.add_parser("show", help="Show one task and its transition history (id or prefix).")
    tasks_show_parser.add_argument("id")
    tasks_sub.add_parser("recover", help="Move tasks stuck in running (after a restart) to blocked.")

    console_parser = subparsers.add_parser("console", help="Run the local operator console (Inbox/Draft/Approval/Audit).")
    console_parser.add_argument("--port", type=int, default=8688, help="Local port to bind on 127.0.0.1 (default 8688).")

    subparsers.add_parser("stats", help="Print the daily budget, token spend, and audit counters.")

    backup_parser = subparsers.add_parser("backup", help="Zip the data-dir roots into one archive.")
    backup_parser.add_argument(
        "destination", nargs="?", help="Directory for the archive (default: ./backups relative to the current directory)."
    )
    restore_parser = subparsers.add_parser("restore", help="Restore a backup archive into the data dir.")
    restore_parser.add_argument("archive", help="Path to a loom-backup-*.zip archive.")
    restore_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing files; without it, any existing target refuses."
    )
    eval_parser = subparsers.add_parser("eval", help="Run the regression eval cases under tests/eval/cases.")
    eval_parser.add_argument(
        "name", nargs="?", help="Run only the case with this name; its own verdict sets the exit code."
    )
    mcp_parser = subparsers.add_parser("mcp", help="Register and manage MCP servers under the same gates.")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_register_parser = mcp_sub.add_parser(
        "register",
        help="Probe the server once, store its schema fingerprint, and record the allowed tool subset.",
    )
    mcp_register_parser.add_argument("--id", required=True, help="Unique server id used in mcp:<id>:<tool> action names.")
    mcp_register_parser.add_argument("--name", required=True, help="Human-readable display name.")
    mcp_register_parser.add_argument(
        "--command",
        dest="mcp_command_line",
        required=True,
        help='How to start the stdio server: a JSON argv array (\'["python", "server.py"]\') or a shell-ish string.',
    )
    mcp_register_parser.add_argument(
        "--allow", required=True, help="Comma-separated tool names the server may be called through."
    )
    mcp_register_parser.add_argument(
        "--credential-env",
        default="",
        help="NAME of the env var holding the server's credential; the value is never stored.",
    )
    mcp_register_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing registration; the new record stays frozen until `loom mcp review`.",
    )
    mcp_sub.add_parser("list", help="List registered MCP servers.")
    mcp_revoke_parser = mcp_sub.add_parser("revoke", help="Disable one server: its tools stop resolving at call time.")
    mcp_revoke_parser.add_argument("id")
    mcp_review_parser = mcp_sub.add_parser(
        "review", help="Re-probe one frozen server, refresh its fingerprint, and clear the freeze."
    )
    mcp_review_parser.add_argument("id")
    args = parser.parse_args(argv)

    if args.command == "version":
        print(loom.__version__)
        return 0
    if args.command == "chat":
        try:
            return _chat(args.thread_id, mcp=args.mcp)
        except ConfigError as exc:
            print(f"config error: {exc}")
            return 1
    if args.command == "routines":
        handlers = {
            "add": _routines_add,
            "list": _routines_list,
            "remove": _routines_remove,
            "run": _routines_run,
        }
        command = handlers[args.routines_command]
    elif args.command == "approvals":
        if args.approvals_command == "list":
            command = _approvals_list
        else:
            decision = "approve" if args.approvals_command == "approve" else "reject"
            command = partial(_approvals_decide, decision=decision)
    elif args.command == "memory":
        if args.memory_command == "search":
            command = _memory_search
        elif args.memory_command == "digest":
            command = _memory_digest
        elif args.memory_command == "episodic":
            command = _memory_episodic
        elif args.memory_command == "compact":
            command = _memory_compact
        elif args.memory_command == "feedback":
            command = _memory_feedback_add if args.feedback_command == "add" else _memory_feedback_list
        else:
            facts_actions = {"set": _memory_facts_set, "del": _memory_facts_del}
            command = facts_actions.get(getattr(args, "facts_command", None), _memory_facts_list)
    elif args.command == "tasks":
        command = {
            "list": _tasks_list,
            "show": _tasks_show,
            "recover": _tasks_recover,
        }[args.tasks_command]
    elif args.command == "triage":
        command = _triage
    elif args.command == "console":
        command = _console
    elif args.command == "worker":
        command = _worker
    elif args.command == "stats":
        command = _stats
    elif args.command == "backup":
        command = _backup
    elif args.command == "restore":
        command = _restore
    elif args.command == "eval":
        command = _eval
    elif args.command == "mcp":
        command = {
            "register": _mcp_register,
            "list": _mcp_list,
            "revoke": _mcp_revoke,
            "review": _mcp_review,
        }[args.mcp_command]
    else:
        return _doctor()

    try:
        return command(args)
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 1
