import imaplib

import pytest

from loom import cli


class _FakeImapClient:
    """Stands in for IMAP4_SSL: a configurable LIST response or login failure."""

    def __init__(self, list_lines=(), login_error=False):
        self.list_lines = list_lines
        self.login_error = login_error

    def login(self, user, password):
        if self.login_error:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] rejected")

    def list(self):
        return "OK", self.list_lines

    def logout(self):
        return "OK", [b"Bye"]


def _listening_port():
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return listener, listener.getsockname()[1]


def _imap_env_file(tmp_path, port):
    return _env_file(
        tmp_path,
        f"LOOM_IMAP_HOST=127.0.0.1\nLOOM_IMAP_PORT={port}\n"
        "LOOM_MAIL_USER=owner@example.com\nLOOM_MAIL_PASSWORD=app-pass\n",
    )


@pytest.mark.parametrize(
    "list_lines",
    [[rb'(\HasNoChildren) "/" "Drafts"'], [rb'(\HasNoChildren \Drafts) "/" "Drafts"']],
)
def test_doctor_imap_special_use_check_warns_only_without_the_drafts_flag(
    monkeypatch, capsys, tmp_path, list_lines
):
    listener, port = _listening_port()
    client = _FakeImapClient(list_lines=list_lines)
    monkeypatch.setattr("loom.cli.imaplib.IMAP4_SSL", lambda host, port, timeout=2: client)
    (tmp_path / ".env").write_text(_imap_env_file(tmp_path, port), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    try:
        code, out = _run_doctor(capsys)
    finally:
        listener.close()

    assert code == 0
    assert "ok     imap" in out
    warned = "does not advertise" in out
    assert warned is (rb"\Drafts" not in list_lines[0]), r"warn exactly when the folder lacks \Drafts"
    assert "Traceback" not in out


def test_doctor_imap_special_use_check_survives_a_login_failure(monkeypatch, capsys, tmp_path):
    listener, port = _listening_port()
    client = _FakeImapClient(list_lines=[rb'(\HasNoChildren) "/" "Drafts"'], login_error=True)
    monkeypatch.setattr("loom.cli.imaplib.IMAP4_SSL", lambda host, port, timeout=2: client)
    (tmp_path / ".env").write_text(_imap_env_file(tmp_path, port), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    try:
        code, out = _run_doctor(capsys)
    finally:
        listener.close()

    assert code == 0
    assert "ok     imap" in out
    assert "does not advertise" not in out
    assert "Traceback" not in out


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


@pytest.mark.parametrize(
    ("url", "secret", "shown"),
    [
        (
            "https://calendar.google.com/calendar/ical/owner%40group/private-cbd8f2basic.ics",
            "cbd8f2",
            "https://calendar.google.com/…",
        ),
        ("https://example.com/ics?token=supersecret1", "supersecret1", "https://example.com/…"),
        ("https://user:topsecret@example.com/ics.ics", "topsecret", "https://example.com/…"),
    ],
)
def test_doctor_output_never_contains_ics_secret(monkeypatch, capsys, tmp_path, url, secret, shown):
    """An unreachable feed prints the FAIL line with scheme and host only."""

    class _DeadTransport:
        def __init__(self, url, timeout=2.0):
            pass

        def fetch_ics(self):
            raise RuntimeError("dead feed (stubbed so the test needs no network)")

    monkeypatch.setattr("loom.cli.HttpCalendarTransport", _DeadTransport)
    (tmp_path / ".env").write_text(_env_file(tmp_path, f"LOOM_ICAL_URL={url}\n"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert code != 0
    assert "FAIL" in out and shown in out
    assert secret not in out, "the ICS secret must never reach doctor output"
    assert "Traceback" not in out


def test_doctor_calendar_line_never_leaks_userinfo_through_the_real_transport(monkeypatch, capsys, tmp_path):
    """No stub: a userinfo password with a space trips http.client's InvalidURL
    before any network call, so this exercises the real transport's redaction."""
    (tmp_path / ".env").write_text(
        _env_file(tmp_path, "LOOM_ICAL_URL=http://user:top secret@127.0.0.1:1/x.ics\n"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    code, out = _run_doctor(capsys)

    assert code != 0
    assert "FAIL" in out and "http://127.0.0.1:1/…" in out
    assert "top secret" not in out, "the userinfo password must never reach doctor output"
    assert "Traceback" not in out
