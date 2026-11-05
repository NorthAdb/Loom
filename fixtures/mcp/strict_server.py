"""A stdio MCP server that refuses tools/list and tools/call until initialize ran.

Used to prove the bridge's call path performs the real MCP handshake
(initialize, then the initialized notification) exactly like the register
path does. stdout carries protocol lines only.
"""

import json
import sys

TOOLS = [
    {
        "name": "ping",
        "description": "Return pong.",
        "inputSchema": {"type": "object", "properties": {}},
    }
]

seen_initialize = False


def _respond(request: dict) -> dict | None:
    global seen_initialize
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        seen_initialize = True
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "strict", "version": "1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if not seen_initialize:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32002, "message": f"initialize required before {method}"},
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "pong"}]}}
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
