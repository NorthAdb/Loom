"""Settings loaded from the environment and an optional .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    loom_env: Literal["dev", "prod"] = Field(validation_alias="LOOM_ENV")
    loom_log_level: Literal["debug", "info", "warning", "error"] = Field(
        default="info", validation_alias="LOOM_LOG_LEVEL"
    )
    loom_data_dir: Path = Field(default=Path("./data"), validation_alias="LOOM_DATA_DIR")
    llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", validation_alias="LLM_MODEL")
    llm_base_url: str = Field(default="", validation_alias="LLM_BASE_URL")


def _describe_errors(exc: ValidationError) -> str:
    # Every field's validation_alias is its uppercase field name, so error
    # locations already read as env var names.
    return "; ".join(f"{str(error['loc'][0]).upper()}: {error['msg']}" for error in exc.errors())


@lru_cache
def get_settings() -> Settings:
    """Load and cache Settings. A missing or invalid value raises ConfigError naming the env var."""
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {_describe_errors(exc)}") from exc
