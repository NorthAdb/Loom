"""A read-only stdio MCP clock server: one deterministic tool, no real clock.

stdout carries protocol lines only; logs go to stderr. time_now() returns a
fixed ISO string so demos and evals stay deterministic.
"""

import json
import sys

TOOLS = [
    {
        "name": "time_now",
        "description": "Return the fixed demo time as an ISO-8601 string (deterministic, no real clock).",
        "inputSchema": {"type": "object", "properties": {}},
    }
]


def _respond(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "clock", "version": "1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": "2026-11-06T09:00:00+08:00"}]},
        }
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main() -> None:
    print("clock server starting", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            print("clock server: ignoring a non-JSON line", file=sys.stderr)
            continue
        response = _respond(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
