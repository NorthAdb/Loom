"""A stdio MCP server whose one tool sleeps forever: the client-timeout fixture.

stdout carries protocol lines only; logs go to stderr. sleep_forever() blocks
for 60 seconds, so a client with a short timeout raises its timeout error long
before any response could arrive.
"""

import json
import sys
import time

TOOLS = [
    {
        "name": "sleep_forever",
        "description": "Sleep for 60 seconds, then return; used to prove the client times out first.",
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
                "serverInfo": {"name": "slow", "version": "1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        time.sleep(60)
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "awake"}]}}
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main() -> None:
    print("slow server starting", file=sys.stderr)
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
