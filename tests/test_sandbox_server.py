import json
import threading
import urllib.request

import pytest

from loom.sandbox import HttpSandboxTransport
from loom.sandbox_server import make_server


@pytest.fixture
def sandbox_service(tmp_path):
    server = make_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", tmp_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def get_json(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(base_url: str, path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_health_reports_ok(sandbox_service):
    base_url, _ = sandbox_service

    assert get_json(base_url, "/health") == {"ok": True}


def test_execute_list_dir_round_trip_over_http(sandbox_service):
    base_url, root = sandbox_service
    (root / "note.txt").write_text("over http", encoding="utf-8")

    status, body = post_json(base_url, "/execute", {"op": "list_dir", "args": {"path": "."}})

    assert status == 200
    assert body["ok"] is True
    assert body["result"]["entries"] == [{"name": "note.txt", "type": "file", "size": 9}]


def test_execute_write_then_read_over_http(sandbox_service):
    base_url, root = sandbox_service

    status, written = post_json(base_url, "/execute", {"op": "write_file", "args": {"path": "a.txt", "content": "hi"}})

    assert status == 200 and written["ok"] is True
    assert (root / "a.txt").read_text(encoding="utf-8") == "hi"
    status, body = post_json(base_url, "/execute", {"op": "read_file", "args": {"path": "a.txt"}})
    assert body["result"]["content"] == "hi"


def test_execute_escaping_path_returns_ok_false_with_message(sandbox_service):
    base_url, root = sandbox_service

    status, body = post_json(base_url, "/execute", {"op": "write_file", "args": {"path": "../escape.txt", "content": "x"}})

    assert status == 400
    assert body["ok"] is False
    assert "escapes the sandbox" in body["error"]
    assert not (root.parent / "escape.txt").exists()


def test_execute_unknown_op_and_bad_body_still_serve(sandbox_service):
    base_url, _ = sandbox_service

    status, body = post_json(base_url, "/execute", {"op": "nope", "args": {}})
    assert status == 400 and "unknown sandbox op" in body["error"]

    request = urllib.request.Request(
        f"{base_url}/execute", data=b"not json", headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(request, timeout=5)
        raised = False
    except urllib.error.HTTPError as exc:
        raised = exc.code == 400
    assert raised
    assert get_json(base_url, "/health") == {"ok": True}


def test_http_transport_surfaces_op_errors_and_results(sandbox_service):
    base_url, root = sandbox_service
    transport = HttpSandboxTransport(base_url)

    (root / "a.txt").write_text("via transport", encoding="utf-8")
    assert transport.call("read_file", {"path": "a.txt"})["content"] == "via transport"

    with pytest.raises(ValueError, match="escapes the sandbox"):
        transport.call("read_file", {"path": "../outside.txt"})


def test_http_transport_unreachable_names_the_compose_hint():
    transport = HttpSandboxTransport("http://127.0.0.1:1")

    with pytest.raises(RuntimeError, match="docker compose up -d"):
        transport.call("list_dir", {"path": "."})
