"""Known-secret scrubbing: values that must never be persisted or shown to the model.

The set of known secrets is: every registered MCP server's credential value,
the Settings mail password, and the Settings LLM API key. Any of them can be
echoed back by an untrusted tool (in its return text, or in arguments a model
was tricked into proposing), so every surface that persists or forwards text —
tool results handed to the model, audit args, graph state that lands in a
checkpoint — scrubs them first. A scrubbed value becomes ``***``; the
surrounding text, its length, and its keys are untouched, so an approval still
shows the full proposal with only the secret values themselves masked (the
same treatment `redact_args` already gave secret-named keys).

Two deliberate limits:

- Values shorter than ``MIN_SCRUB_LENGTH`` are not substring-scrubbed. A
  four-character credential such as ``bill`` is also an ordinary word, and
  replacing it would silently rewrite legitimate text (``heating bill due``
  becoming ``heating *** due``) while the call still reports success. Short
  values are still protected by the other layers: they are never in another
  server's child environment, and a secret-named argument key is always
  masked by `redact_args` regardless of length.
- Nothing here lengthens the child-environment whitelist or widens which
  credential a server receives; this module only removes text.
"""

import os
from contextlib import suppress
from pathlib import Path
from typing import Any

MIN_SCRUB_LENGTH = 8

MASK = "***"

# The environment variables the Settings fields are loaded from; used when a
# caller (an AuditLog, say) has a data dir but no Settings object at hand.
_SETTINGS_ENV_NAMES = ("LOOM_MAIL_PASSWORD", "LLM_API_KEY")


def _eligible(values) -> "list[str]":
    """The distinct scrub-eligible values, longest first so overlaps mask fully."""
    seen = {value for value in values if isinstance(value, str) and len(value) >= MIN_SCRUB_LENGTH}
    return sorted(seen, key=len, reverse=True)


def known_secret_values(settings: Any = None, data_dir: "Path | None" = None) -> "list[str]":
    """Every currently known secret value, scrub-eligible and longest-first.

    Sources: the Settings mail password and LLM key (from the object when one
    is supplied, else from the environment variables they load from), and the
    credential value of every MCP server registered under `data_dir` (or the
    Settings data dir). Empty and unset values contribute nothing. Reading the
    registry is best-effort: a missing or malformed store must never break a
    scrub, it just contributes no values.
    """
    values: list[str] = []
    if settings is not None:
        values.append(getattr(settings, "mail_password", "") or "")
        values.append(getattr(settings, "llm_api_key", "") or "")
        if data_dir is None:
            data_dir = getattr(settings, "loom_data_dir", None)
    else:
        for name in _SETTINGS_ENV_NAMES:
            values.append(os.environ.get(name, ""))
    if data_dir is not None:
        # Scrubbing must not fail on a missing or malformed registry file:
        # it just contributes no values.
        with suppress(Exception):
            from loom.tools.mcp.registry import McpServerStore

            for record in McpServerStore(Path(data_dir)).list():
                if record.credential_env:
                    values.append(os.environ.get(record.credential_env, ""))
    return _eligible(values)


def scrub_text(text: str, values) -> str:
    """Replace every eligible known-secret value in `text` with the mask."""
    for value in _eligible(values):
        text = text.replace(value, MASK)
    return text


def scrub_value(value: Any, values) -> Any:
    """Deep-scrub strings inside nested dicts/lists/tuples; other values pass through."""
    eligible = _eligible(values)
    if not eligible:
        return value
    if isinstance(value, str):
        return scrub_text(value, eligible)
    if isinstance(value, dict):
        return {key: scrub_value(item, eligible) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_value(item, eligible) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_value(item, eligible) for item in value)
    return value
