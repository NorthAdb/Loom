"""Chat model construction from settings."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from loom.config import ConfigError, Settings


def make_model(settings: Settings) -> BaseChatModel:
    """Build the chat model; LLM_BASE_URL empty means the OpenAI default endpoint."""
    if not settings.llm_api_key:
        raise ConfigError("LLM_API_KEY is empty; set it in .env or the environment to chat")
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
    )
