"""Chat model construction from settings."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from loom.core.config import ConfigError, Settings


class InertChatModel(BaseChatModel):
    """A stand-in model for runs that never invoke one.

    The reject path of an approval resume goes gate -> aborted -> END without
    a single model call, but LangGraph still needs a compiled graph with the
    same shape to resume from the checkpoint, and the build binds tools. This
    model exists so a human can refuse work even when no LLM key is
    configured or the key is broken; it raises if anything ever invokes it.
    """

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "inert"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("the inert model was invoked; only the reject path may build a graph without a key")


def make_model(settings: Settings) -> BaseChatModel:
    """Build the chat model; LLM_BASE_URL empty means the OpenAI default endpoint."""
    if not settings.llm_api_key:
        raise ConfigError("LLM_API_KEY is empty; set it in .env or the environment to chat")
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
    )
