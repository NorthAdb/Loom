"""A stdio MCP server whose one tool reports the environment KEYS it can see.

Used to prove the child-process environment is the minimal allowlist plus the
one credential variable: the result text carries variable NAMES only, never
values. stdout carries protocol lines only; logs go to stderr.
"""

import json
import os
import sys

TOOLS = [
    {
        "name": "dump_env",
        "description": "Return the names of the environment variables this server can see.",
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
                "serverInfo": {"name": "env", "version": "1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        keys = ",".join(sorted(os.environ))
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": keys}]}}
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        response = _respond(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
