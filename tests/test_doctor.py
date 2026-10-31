from loom import cli


def _run_doctor(capsys):
    code = cli.main(["doctor"])
    return code, capsys.readouterr().out


def _env_file(tmp_path, extra=""):
    return f"LOOM_ENV=dev\nLOOM_DATA_DIR={(tmp_path / 'data').as_posix()}\n" + extra


def test_doctor_on_clean_env_fails_and_lists_missing(capsys):
    code, out = _run_doctor(capsys)

    assert code != 0
    assert "MISSING" in out
    assert ".env" in out
    assert "LOOM_ENV" in out


def test_doctor_with_valid_env_passes_and_warns_about_api_key(monkeypatch, capsys, tmp_path):
    (tmp_path / ".env").write_text(
        f"LOOM_ENV=dev\nLOOM_DATA_DIR={(tmp_path / 'data').as_posix()}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    code, out = _run_doctor(capsys)

    assert code == 0
    assert "warn" in out
    assert "LLM_API_KEY" in out
    assert (tmp_path / "data").is_dir()


def test_doctor_with_invalid_loom_env_fails_naming_variable(monkeypatch, capsys, tmp_path):
    (tmp_path / ".env").write_text("LOOM_ENV=bogus\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code, out = _run_doctor(capsys)

    assert code != 0
    assert "LOOM_ENV" in out


def test_doctor_creates_the_sandbox_root_when_no_url_is_set(monkeypatch, capsys, tmp_path):
    (tmp_path / ".env").write_text(f"LOOM_ENV=dev\nLOOM_DATA_DIR={(tmp_path / 'data').as_posix()}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code, out = _run_doctor(capsys)

    assert code == 0
    assert "sandbox root" in out
    assert (tmp_path / "sandbox").is_dir()


def test_doctor_fails_when_configured_sandbox_url_is_unreachable(monkeypatch, capsys, tmp_path):
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()
    (tmp_path / ".env").write_text(
        f"LOOM_ENV=dev\nLOOM_DATA_DIR={(tmp_path / 'data').as_posix()}\n"
        f"LOOM_SANDBOX_URL=http://127.0.0.1:{free_port}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    code, out = _run_doctor(capsys)

    assert code != 0
    assert "sandbox service" in out
    assert "FAIL" in out


def test_doctor_skips_calendar_and_imap_lines_when_unset(monkeypatch, capsys, tmp_path):
    (tmp_path / ".env").write_text(_env_file(tmp_path), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code, out = _run_doctor(capsys)

    assert code == 0
    assert "calendar feed" not in out
    assert "imap" not in out


def test_doctor_calendar_line_fails_on_unreachable_feed(monkeypatch, capsys, tmp_path):
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()
    (tmp_path / ".env").write_text(
        _env_file(tmp_path, f"LOOM_ICAL_URL=http://127.0.0.1:{free_port}/feed.ics\n"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    code, out = _run_doctor(capsys)

    assert code != 0
    assert "calendar feed" in out
    assert "FAIL" in out


def test_doctor_calendar_line_reports_a_serving_feed(monkeypatch, capsys, tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (tmp_path / ".env").write_text(
        _env_file(tmp_path, f"LOOM_ICAL_URL=http://127.0.0.1:{server.server_address[1]}/feed.ics\n"),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    try:
        code, out = _run_doctor(capsys)
    finally:
        server.shutdown()
        server.server_close()

    assert code == 0
    assert "ok     calendar feed" in out


def test_doctor_imap_line_fails_on_unreachable_host(monkeypatch, capsys, tmp_path):
    (tmp_path / ".env").write_text(
        _env_file(tmp_path, "LOOM_IMAP_HOST=127.0.0.1\nLOOM_IMAP_PORT=1\n"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    code, out = _run_doctor(capsys)

    assert code != 0
    assert "imap 127.0.0.1:1" in out
    assert "FAIL" in out


def test_doctor_imap_line_reports_a_listening_host(monkeypatch, capsys, tmp_path):
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    (tmp_path / ".env").write_text(
        _env_file(tmp_path, f"LOOM_IMAP_HOST=127.0.0.1\nLOOM_IMAP_PORT={port}\n"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    code, out = _run_doctor(capsys)

    assert code == 0
    assert f"ok     imap 127.0.0.1:{port}" in out


def test_doctor_tolerates_a_non_integer_imap_port_without_crashing(capsys):
    """A broken LOOM_IMAP_PORT must surface as a FAIL line, not a traceback."""
    import os

    os.environ["LOOM_IMAP_HOST"] = "imap.example.com"
    os.environ["LOOM_IMAP_PORT"] = "not-a-number"

    code = cli.main(["doctor"])

    out = capsys.readouterr().out
    assert code != 0
    assert "imap" in out
    assert "FAIL" in out
