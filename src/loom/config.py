"""Settings loaded from the environment and an optional .env file."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


_DEFAULT_IMAP_PORT = 993
_DEFAULT_MAIL_FOLDER = "Drafts"
_DEFAULT_TIMEZONE = "Asia/Hong_Kong"

# Leaf segments a drafts folder must not have: LOOM_MAIL_FOLDER points at the
# drafts mailbox, and a wrong pick here would let draft replacement delete or
# expunge unrelated mail. Matched case-insensitively against the last segment
# (after "/" or "." hierarchy separators) of the configured folder.
_FORBIDDEN_MAIL_FOLDER_LEAVES = {"inbox", "sent", "trash", "junk", "deleted"}


def _validate_mail_folder(name: str) -> None:
    # A control character (CR/LF/NUL/TAB/DEL) inside a mailbox name could
    # terminate it early and inject an IMAP command, so reject it outright.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ConfigError(
            f"LOOM_MAIL_FOLDER: {name!r} must not contain control characters "
            "(a control character in a mailbox name could inject an IMAP command)"
        )
    leaf = re.split(r"[/.]", name.strip())[-1].lower()
    if leaf in _FORBIDDEN_MAIL_FOLDER_LEAVES:
        raise ConfigError(
            f"LOOM_MAIL_FOLDER: {name!r} is not a drafts folder; "
            "a drafts folder is required (e.g. Drafts or [Gmail]/Drafts)"
        )


class Settings(BaseSettings):
    # populate_by_name: the newer fields (mail_user, ...) are not uppercase
    # variants of their aliases, so direct construction by field name needs this.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    loom_env: Literal["dev", "prod"] = Field(validation_alias="LOOM_ENV")
    loom_log_level: Literal["debug", "info", "warning", "error"] = Field(
        default="info", validation_alias="LOOM_LOG_LEVEL"
    )
    loom_data_dir: Path = Field(default=Path("./data"), validation_alias="LOOM_DATA_DIR")
    loom_sandbox_root: Path = Field(default=Path("./sandbox"), validation_alias="LOOM_SANDBOX_ROOT")
    loom_sandbox_url: str = Field(default="", validation_alias="LOOM_SANDBOX_URL")
    llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", validation_alias="LLM_MODEL")
    llm_base_url: str = Field(default="", validation_alias="LLM_BASE_URL")
    memory_enabled: bool = Field(default=True, validation_alias="LOOM_MEMORY_ENABLED")
    memory_inject_limit: int = Field(default=5, validation_alias="LOOM_MEMORY_INJECT_LIMIT")
    ical_url: str = Field(default="", validation_alias="LOOM_ICAL_URL")
    imap_host: str = Field(default="", validation_alias="LOOM_IMAP_HOST")
    imap_port: int = Field(default=_DEFAULT_IMAP_PORT, validation_alias="LOOM_IMAP_PORT")
    mail_user: str = Field(default="", validation_alias="LOOM_MAIL_USER")
    mail_password: str = Field(default="", validation_alias="LOOM_MAIL_PASSWORD")
    mail_folder: str = Field(default=_DEFAULT_MAIL_FOLDER, validation_alias="LOOM_MAIL_FOLDER")
    timezone_name: str = Field(default=_DEFAULT_TIMEZONE, validation_alias="LOOM_TIMEZONE")

    @field_validator("imap_port", "mail_folder", "timezone_name", mode="before")
    @classmethod
    def _empty_means_default(cls, value, info):
        # An empty env var is "unset", so the declared default applies. Without
        # this, an empty LOOM_IMAP_PORT fails int parsing and an empty
        # LOOM_MAIL_FOLDER / LOOM_TIMEZONE would override its default with "".
        defaults = {"imap_port": _DEFAULT_IMAP_PORT, "mail_folder": _DEFAULT_MAIL_FOLDER, "timezone_name": _DEFAULT_TIMEZONE}
        if value in ("", None):
            return defaults[info.field_name]
        return value

    def __init__(self, *args, **values) -> None:
        super().__init__(*args, **values)
        # ConfigError (not a pydantic ValidationError) so doctor and chat both
        # surface the message verbatim, like the timezone check in get_settings.
        _validate_mail_folder(self.mail_folder)


def _describe_errors(exc: ValidationError) -> str:
    # Every field's validation_alias is its uppercase field name, so error
    # locations already read as env var names.
    return "; ".join(f"{str(error['loc'][0]).upper()}: {error['msg']}" for error in exc.errors())


@lru_cache
def get_settings() -> Settings:
    """Load and cache Settings. A missing or invalid value raises ConfigError naming the env var."""
    try:
        settings = Settings()
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {_describe_errors(exc)}") from exc
    try:
        ZoneInfo(settings.timezone_name)
    except (KeyError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ConfigError(f"LOOM_TIMEZONE: unknown timezone {settings.timezone_name!r}") from exc
    return settings
