"""Command line entry points: loom version, doctor, chat, routines, approvals, memory, worker."""

import argparse
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from langchain_core.messages import HumanMessage
from langgraph.types import Command

import loom
from loom.calendar import HttpCalendarTransport, redact_url
from loom.config import ConfigError, Settings, get_settings
from loom.logging import set_run_id, setup_logging
from loom.memory import FactsStore, build_semantic_memory
from loom.pending import PENDING, PendingStore
from loom.routines import Routine, RoutineStore
from loom.runner import resume_pending, run_routine
from loom.runtime import open_graph

logger = logging.getLogger("loom")

_MIN_PYTHON = (3, 11)

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


def _ical_reachable(url: str) -> bool:
    """One GET of the ICS feed; any transport error means unreachable."""
    try:
        HttpCalendarTransport(url, timeout=2).fetch_ics()
    except RuntimeError:
        return False
    return True


def _imap_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


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
        if _ical_reachable(ical_url):
            print(f"{_OK}calendar feed {shown}")
        else:
            print(f"{_FAIL}calendar feed {shown} (no ICS response; check LOOM_ICAL_URL)")
            failed = True

    imap_host = settings.imap_host if settings else os.environ.get("LOOM_IMAP_HOST", "")
    if imap_host:
        imap_port = settings.imap_port if settings else int(os.environ.get("LOOM_IMAP_PORT") or "993")
        if _imap_reachable(imap_host, imap_port):
            print(f"{_OK}imap {imap_host}:{imap_port}")
        else:
            print(f"{_FAIL}imap {imap_host}:{imap_port} (no TCP connection; check LOOM_IMAP_HOST and LOOM_IMAP_PORT)")
            failed = True

    return 1 if failed else 0


def chat_session(graph, thread_id: str, read: Callable[[str], str], emit: Callable[[str], None]) -> None:
    """Drive one REPL over a compiled graph: turn, gate approval loop, answer."""
    config = {"configurable": {"thread_id": thread_id}}
    while True:
        text = read("you> ").strip()
        if not text:
            continue
        if text.lower() in ("quit", "exit"):
            return

        run_id = uuid.uuid4().hex
        set_run_id(run_id)
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


def _chat(thread_id: str | None, read: Callable[[str], str] = input, emit: Callable[[str], None] = print) -> int:
    settings = get_settings()
    setup_logging(settings)
    with open_graph(settings) as graph:
        session_id = thread_id or uuid.uuid4().hex
        emit(f"thread {session_id}")
        emit("type a message; side-effect tools pause for approve/reject; 'quit' exits")
        try:
            chat_session(graph, session_id, read, emit)
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
    print(f"thread {outcome.thread_id}")
    if outcome.outcome == PENDING:
        print(f"waiting for approval: {outcome.pending_id}")
        print(f"resolve with: loom approvals approve {outcome.pending_id}  or  loom approvals reject {outcome.pending_id}")
    else:
        print(outcome.answer)
        print("completed")
    return 0


def _approvals_list(args: argparse.Namespace) -> int:
    settings = get_settings()
    waiting = PendingStore(settings.loom_data_dir).list_pending()
    if not waiting:
        print("(none)")
        return 0
    for record in waiting:
        tools = ", ".join(item["name"] for item in record.tools)
        print(f"{record.pending_id}  routine={record.routine_id}  tools=[{tools}]  created={record.created_at}")
    return 0


def _approvals_decide(args: argparse.Namespace, decision: str) -> int:
    settings = get_settings()
    setup_logging(settings)
    pending = PendingStore(settings.loom_data_dir)
    try:
        outcome = resume_pending(args.pending_id, decision, settings, pending)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loom", description="Loom personal agent CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="Print the package version.")
    subparsers.add_parser("doctor", help="Check the local environment.")
    chat_parser = subparsers.add_parser("chat", help="Chat with the agent.")
    chat_parser.add_argument("--thread-id", help="Resume an existing thread from its checkpoint.")

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
    args = parser.parse_args(argv)

    if args.command == "version":
        print(loom.__version__)
        return 0
    if args.command == "chat":
        try:
            return _chat(args.thread_id)
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
        else:
            facts_actions = {"set": _memory_facts_set, "del": _memory_facts_del}
            command = facts_actions.get(getattr(args, "facts_command", None), _memory_facts_list)
    elif args.command == "worker":
        command = _worker
    else:
        return _doctor()

    try:
        return command(args)
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 1
