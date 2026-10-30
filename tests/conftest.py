import pytest

from loom.config import get_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Isolate tests from the host environment and the repository directory."""
    get_settings.cache_clear()
    for name in ("LOOM_ENV", "LOOM_LOG_LEVEL", "LOOM_DATA_DIR", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    yield
    get_settings.cache_clear()
