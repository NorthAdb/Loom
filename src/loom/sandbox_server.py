"""HTTP server for the file sandbox: POST /execute runs one jailed op.

Stdlib only; the container entrypoint runs this file directly. It imports nothing
from Loom except the sandbox dispatcher, so the image ships two files.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from loom.sandbox import handle
except ImportError:  # the container ships these two files flat, without the loom package
    from sandbox import handle


class SandboxHandler(BaseHTTPRequestHandler):
    """One request in, one JSON response out; errors answer 400 and keep serving."""

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _execute(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            result = handle(request["op"], request.get("args") or {}, self.server.root)
            self._send(200, {"ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001 - a failed op answers 400; the server keeps serving
            self._send(400, {"ok": False, "error": str(exc)})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False, "error": f"not found: {self.path}"})

    def do_POST(self) -> None:
        if self.path == "/execute":
            self._execute()
        else:
            self._send(404, {"ok": False, "error": f"not found: {self.path}"})


class SandboxServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, host: str = "0.0.0.0", port: int = 8765) -> None:
        self.root = Path(root)
        super().__init__((host, port), SandboxHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Loom file sandbox HTTP server.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0", help="0.0.0.0 so a published compose port reaches the container")
    parser.add_argument("--root", default="./sandbox", help="Directory every op is jailed to.")
    args = parser.parse_args()

    server = SandboxServer(Path(args.root), host=args.host, port=args.port)
    print(f"loom sandbox serving {server.root} on {args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
