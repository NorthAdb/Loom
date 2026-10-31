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
