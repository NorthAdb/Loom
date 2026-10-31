"""Command line entry points: loom version, loom doctor, loom chat."""

import argparse
import logging
import os
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

import loom
from loom.config import ConfigError, Settings, get_settings
from loom.graph import build_graph
from loom.llm import make_model
from loom.logging import set_run_id, setup_logging

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
            return bool(response) and response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
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
    model = make_model(settings)
    ckpt_dir = Path(settings.loom_data_dir) / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(ckpt_dir / "threads.sqlite") as checkpointer:
        graph = build_graph(model, settings, checkpointer)
        session_id = thread_id or uuid.uuid4().hex
        emit(f"thread {session_id}")
        emit("type a message; side-effect tools pause for approve/reject; 'quit' exits")
        try:
            chat_session(graph, session_id, read, emit)
        except (EOFError, KeyboardInterrupt):
            pass
    emit("goodbye")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loom", description="Loom personal agent CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="Print the package version.")
    subparsers.add_parser("doctor", help="Check the local environment.")
    chat_parser = subparsers.add_parser("chat", help="Chat with the agent.")
    chat_parser.add_argument("--thread-id", help="Resume an existing thread from its checkpoint.")
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
    return _doctor()
