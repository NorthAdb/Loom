"""A minimal MCP client: line-delimited JSON-RPC 2.0 over stdio, no SDK.

One request per line, one response per line, matched by an integer id. A
background thread reads stdout lines into a queue — the reliable timeout
mechanism on Windows, where selectors do not work on pipes. stderr is drained
and discarded on purpose: a server's stderr can carry secrets, so it never
enters an exception, a log, or the audit (there is no capped tail kept
anywhere). Responses are capped at 1 MiB; an oversized or malformed response
raises McpError with a message that names the problem, never the payload.
"""

import itertools
import json
import os
import queue
import subprocess
import threading
import time
from contextlib import suppress
from typing import Self

from loom.tools.mcp.registry import McpError

_PROTOCOL_VERSION = "2024-11-05"
_MAX_RESPONSE_CHARS = 1024 * 1024
_ERROR_DETAIL_CAP = 500


def _client_info() -> dict:
    from loom import __version__

    return {"name": "loom", "version": __version__}


def _first_text(result: dict) -> str | None:
    """The text of the first content item, when the server sent one."""
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            return first["text"]
    return None


class McpClient:
    """One stdio MCP server session; start/stop explicitly or use as a context manager.

    `credential_env` names the environment variable that carries the server's
    credential: the child process receives that name mapped to its current
    value (or the empty string when unset). The value is never read by this
    class, never logged, and never stored.
    """

    def __init__(self, command: "list[str]", *, credential_env: str = "", timeout: float = 10.0) -> None:
        self._command = [str(part) for part in command]
        self._credential_env = credential_env
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._responses: queue.Queue = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._ids = itertools.count(1)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.stop()
        return False

    def start(self) -> None:
        if self._proc is not None:
            raise McpError("mcp client is already started")
        env = dict(os.environ)
        if self._credential_env:
            env[self._credential_env] = os.environ.get(self._credential_env, "")
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self._threads = [
            threading.Thread(target=self._drain_responses, daemon=True),
            threading.Thread(target=self._drain_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with suppress(Exception):
            if proc.stdin:
                proc.stdin.close()
        if proc.poll() is None:
            with suppress(Exception):
                proc.wait(timeout=2)
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            with suppress(Exception):
                proc.wait(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            with suppress(Exception):
                if stream:
                    stream.close()
        for thread in self._threads:
            thread.join(timeout=1)
        self._threads = []

    def initialize(self) -> dict:
        """The MCP handshake; returns the server's initialize result."""
        params = {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _client_info(),
        }
        return self._request("initialize", params)

    def list_tools(self) -> "list[dict]":
        """The server's advertised tool list, as-is from the tools/list result."""
        self._notify("notifications/initialized")
        tools = self._request("tools/list", {}).get("tools")
        if not isinstance(tools, list):
            raise McpError("mcp server sent a malformed response (no tools list)")
        return tools

    def call_tool(self, name: str, arguments: dict) -> str:
        """Call one tool and return its text as data; an isError result raises McpError."""
        result = self._request("tools/call", {"name": name, "arguments": dict(arguments)})
        if result.get("isError"):
            raise McpError(_first_text(result) or "mcp tool call failed")
        text = _first_text(result)
        return text if text is not None else json.dumps(result, ensure_ascii=False)

    def _notify(self, method: str) -> None:
        notification = {"jsonrpc": "2.0", "method": method}
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpError("mcp client is not started")
        try:
            proc.stdin.write(json.dumps(notification, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError("mcp server exited unexpectedly") from exc

    def _request(self, method: str, params: dict) -> dict:
        """One JSON-RPC round trip; the deadline covers the wait, not the write."""
        proc = self._proc
        if proc is None:
            raise McpError("mcp client is not started")
        request_id = next(self._ids)
        request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError("mcp server exited unexpectedly") from exc
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if proc.poll() is not None:
                    raise McpError("mcp server exited unexpectedly")
                raise McpError(f"mcp request '{method}' timed out after {self._timeout:g}s")
            try:
                kind, message = self._responses.get(timeout=remaining)
            except queue.Empty:
                continue
            if kind != "message":
                if kind == "closed":
                    # EOF on stdout: the response channel is gone, whether the
                    # process is reaped yet or not. The session is over.
                    raise McpError("mcp server exited unexpectedly")
                raise McpError(f"mcp server sent a malformed response ({kind})")
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue  # a notification, or a stray response; keep waiting
            error = message.get("error")
            if error is not None:
                detail = error.get("message", "unknown error") if isinstance(error, dict) else "unknown error"
                raise McpError(f"mcp server error: {str(detail)[:_ERROR_DETAIL_CAP]}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def _drain_responses(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = proc.stdout.readline(_MAX_RESPONSE_CHARS + 1)
                if not line:
                    break
                if len(line) > _MAX_RESPONSE_CHARS:
                    self._responses.put(("oversized", None))
                    while line and not line.endswith("\n"):
                        line = proc.stdout.readline(_MAX_RESPONSE_CHARS + 1)
                        if not line:
                            break
                    continue
                text = line.strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    self._responses.put(("malformed", None))
                    continue
                self._responses.put(("message", message))
        except (OSError, ValueError):
            pass
        self._responses.put(("closed", None))

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for _line in proc.stderr:
                pass  # drained and discarded: stderr can carry secrets and never reaches exceptions or logs
        except (OSError, ValueError):
            pass
