"""Gate 4 guards: the browser surface is read-only until its single gated submit.

Mirrors tests/test_gate3_guards.py: the registry's browser tools and flags, a
source-level ban on the verbs that could act, download, or rewrite traffic
outside the one gated submit, and proof that the submit implementation owns
the module's only click.
"""

from pathlib import Path

import pytest

from loom.config import Settings
from loom.tools import make_tools

_SRC = Path(__file__).resolve().parents[1] / "src" / "loom"

# Verbs that would let the browser execute page code, fetch downloads, hit
# APIs, rewrite traffic, or grant itself capabilities: none of them belong on
# the fill path. page.route is absent on purpose: it is the transport's own
# request firewall, allowed only inside browser.py.
FORBIDDEN_BROWSER_SYMBOLS = (
    "page.evaluate",
    "page.request",
    "expect_download",
    "download",
    "api_request",
    "set_input_files",
    "grant_permissions",
)


def browser_source() -> str:
    return (_SRC / "browser.py").read_text(encoding="utf-8")


def make_registry(tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    return make_tools(settings)


def test_registry_has_sixteen_tools_with_exact_browser_flags(tmp_path):
    registry = make_registry(tmp_path)

    assert len(registry) == 16
    assert set(registry) == {
        "get_time", "echo", "list_dir", "read_file", "write_file", "mkdir", "move_file", "delete_file",
        "propose_sandbox_note", "calendar_list_events", "calendar_summarize_range",
        "mail_create_draft", "mail_update_draft",
        "browser_open", "browser_fill", "browser_submit",
    }
    assert registry["browser_open"].side_effect is False
    assert registry["browser_fill"].side_effect is False
    assert registry["browser_submit"].side_effect is True


@pytest.mark.parametrize("symbol", FORBIDDEN_BROWSER_SYMBOLS)
def test_forbidden_browser_verbs_never_appear_in_browser_source(symbol):
    assert symbol not in browser_source(), f"forbidden verb in browser.py: {symbol}"


def test_the_gated_submit_owns_the_only_click_in_browser_source():
    assert browser_source().count(".click(") == 1, "submit's single control press is the module's only click"
