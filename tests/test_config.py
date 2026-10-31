from pathlib import Path

import pytest

from loom.config import ConfigError, get_settings


def test_loads_from_environment(monkeypatch):
    monkeypatch.setenv("LOOM_ENV", "dev")

    settings = get_settings()

    assert settings.loom_env == "dev"
    assert settings.loom_log_level == "info"
    assert settings.loom_data_dir == Path("./data")
    assert settings.loom_sandbox_root == Path("./sandbox")
    assert settings.loom_sandbox_url == ""
    assert settings.llm_api_key == ""


def test_llm_api_key_reads_its_exact_env_name(monkeypatch):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LLM_API_KEY", "key-for-tests")

    assert get_settings().llm_api_key == "key-for-tests"


def test_missing_loom_env_names_the_variable():
    with pytest.raises(ConfigError, match="LOOM_ENV"):
        get_settings()


def test_invalid_loom_env_names_the_variable(monkeypatch):
    monkeypatch.setenv("LOOM_ENV", "bogus")

    with pytest.raises(ConfigError, match="LOOM_ENV"):
        get_settings()


def test_mail_and_calendar_fields_default_to_unconfigured(monkeypatch):
    monkeypatch.setenv("LOOM_ENV", "dev")

    settings = get_settings()

    assert settings.ical_url == ""
    assert settings.imap_host == ""
    assert settings.imap_port == 993
    assert settings.mail_user == ""
    assert settings.mail_password == ""
    assert settings.mail_folder == "Drafts"
    assert settings.timezone_name == "Asia/Hong_Kong"


def test_empty_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_IMAP_PORT", "")
    monkeypatch.setenv("LOOM_MAIL_FOLDER", "")
    monkeypatch.setenv("LOOM_TIMEZONE", "")

    settings = get_settings()

    assert settings.imap_port == 993
    assert settings.mail_folder == "Drafts"
    assert settings.timezone_name == "Asia/Hong_Kong"


def test_unknown_timezone_names_loom_timezone(monkeypatch):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_TIMEZONE", "Mars/Olympus_Mons")

    with pytest.raises(ConfigError, match="LOOM_TIMEZONE"):
        get_settings()
