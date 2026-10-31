import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from loom.config import get_settings


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted AIMessages in order; bind_tools returns the model itself."""

    def bind_tools(self, tools, **kwargs):
        return self


def tool_call_msg(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    """Build an AIMessage carrying one tool call, as a model would emit."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def read_audit(data_dir) -> list[dict]:
    """Parse the audit JSONL a test just wrote."""
    path = next((data_dir / "audit").glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Isolate tests from the host environment and the repository directory."""
    get_settings.cache_clear()
    for name in (
        "LOOM_ENV", "LOOM_LOG_LEVEL", "LOOM_DATA_DIR", "LOOM_SANDBOX_ROOT", "LOOM_SANDBOX_URL",
        "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL",
        "LOOM_MEMORY_ENABLED", "LOOM_MEMORY_INJECT_LIMIT", "MEM0_TELEMETRY",
    ):
        monkeypatch.delenv(name, raising=False)
    # Memory tests inject fakes through the seams; everything else runs with the
    # semantic layer off so no test needs an API key or a Mem0 endpoint.
    monkeypatch.setenv("LOOM_MEMORY_ENABLED", "false")
    monkeypatch.chdir(tmp_path)
    yield
    get_settings.cache_clear()
