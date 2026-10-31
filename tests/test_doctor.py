from loom import cli


def _run_doctor(capsys):
    code = cli.main(["doctor"])
    return code, capsys.readouterr().out


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
