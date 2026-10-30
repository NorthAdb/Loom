"""Command line entry points: loom version, loom doctor."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import loom
from loom.config import ConfigError, Settings, get_settings

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

    api_key = settings.llm_api_key if settings else os.environ.get("LLM_API_KEY", "")
    if api_key:
        print(f"{_OK}LLM_API_KEY present")
    else:
        print(f"{_WARN}LLM_API_KEY not set (optional in Phase 0)")

    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loom", description="Loom personal agent CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="Print the package version.")
    subparsers.add_parser("doctor", help="Check the local environment.")
    args = parser.parse_args(argv)

    if args.command == "version":
        print(loom.__version__)
        return 0
    return _doctor()
