"""A stdio MCP server with two tools: an echo and a credential reveal.

`reveal_token` returns the server's own credential value (the one the
credential handle forwarded), which is exactly the shape the audit's P0-3
acceptance probes: an approved call of a secret-returning tool must leave no
credential in the model-visible text or in a backup. The bridge scrubs known
values before the text reaches the model. stdout carries protocol lines only.
"""

import json
import os
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Return the text argument unchanged.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "reveal_token",
        "description": "Return this server's own credential value from its environment.",
        "inputSchema": {"type": "object", "properties": {}},
    },
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
                "serverInfo": {"name": "echo", "version": "1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = request.get("params") or {}
        arguments = params.get("arguments") or {}
        if str(params.get("name")) == "reveal_token":
            text = os.environ.get("LOOM_ECHO_TOKEN", "unset")
        else:
            text = str(arguments.get("text", ""))
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}}
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
