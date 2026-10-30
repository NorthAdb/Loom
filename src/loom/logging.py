"""Structured JSON logging with run ids and secret redaction. Stdlib only."""

import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loom.config import Settings

_run_id: ContextVar[str] = ContextVar("loom_run_id", default="-")


def set_run_id(run_id: str) -> "Token[str]":
    """Set the run id attached to log records; returns the reset token."""
    return _run_id.set(run_id)

_STANDARD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info", "taskName",
    "thread", "threadName",
})

_SENSITIVE = re.compile(r"key|token|secret|password|authorization", re.IGNORECASE)


def is_sensitive(name: str) -> bool:
    """Report whether a field name looks secret-bearing."""
    return bool(_SENSITIVE.search(name))


class RunContextFilter(logging.Filter):
    """Attach the current run id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        return True


class RedactionFilter(logging.Filter):
    """Replace values of extras whose names look secret-bearing with '***'."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in list(vars(record)):
            if name in _STANDARD_ATTRS:
                continue
            if is_sensitive(name):
                setattr(record, name, "***")
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, message, run_id, extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", "-"),
        }
        for name, value in vars(record).items():
            if name in _STANDARD_ATTRS or name == "run_id":
                continue
            payload[name] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(settings: Settings) -> None:
    """Log JSON lines to stderr and append them to <data dir>/logs/loom.log."""
    log_dir = Path(settings.loom_data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = JsonFormatter()
    filters = (RunContextFilter(), RedactionFilter())

    def wire(handler: logging.Handler) -> logging.Handler:
        handler.setFormatter(formatter)
        for log_filter in filters:
            handler.addFilter(log_filter)
        return handler

    root = logging.getLogger()
    for stale in list(root.handlers):
        root.removeHandler(stale)
    root.setLevel(logging.getLevelNamesMapping()[settings.loom_log_level.upper()])
    root.addHandler(wire(logging.StreamHandler(sys.stderr)))
    root.addHandler(wire(logging.FileHandler(log_dir / "loom.log", encoding="utf-8")))
