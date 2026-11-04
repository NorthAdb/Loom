"""The package layout is a contract: the root stays clean and dependencies flow one way.

The handoff's close-out bar is the §2.3 tree with no reverse dependencies.
These checks are structural, not behavioral, so they read source paths and
import lines, the same style as the send-symbol guards.
"""

from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "loom"


def test_the_package_root_holds_only_the_init():
    assert [path.name for path in _SRC.glob("*.py")] == ["__init__.py"]


def _imports_of(package: str) -> "list[str]":
    lines = []
    for path in sorted((_SRC / package).rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("from loom.", "import loom.")) and "loomo" not in stripped:
                lines.append(f"{path.name}: {stripped}")
    return lines


def test_tools_never_import_the_console_or_the_cli():
    offenders = [line for line in _imports_of("tools") if "loom.console" in line or "loom.cli" in line]
    assert offenders == []


def test_inbound_only_creates_tasks_and_scopes_and_never_imports_the_console_or_tools():
    allowed_prefixes = (
        "from loom.audit",
        "from loom.gateway",
        "from loom.inbound",
        "from loom.ops.routines",
        "from loom.tasks",
    )
    offenders = [
        line
        for line in _imports_of("inbound")
        if "loom.console" in line or "loom.tools" in line or "loom.cli" in line
        or not line.split(": ", 1)[1].startswith(allowed_prefixes)
    ]
    assert offenders == []


def test_console_imports_no_business_module_it_should_not_own():
    """The console is skin: it reads stores and the runner, never the gateway's mint or the graph build."""
    banned = ("issue_scope", "build_graph", "ToolGateway", "FactsStore(")
    source = "\n".join(path.read_text(encoding="utf-8") for path in (_SRC / "console").rglob("*.py"))
    assert not any(banned_item in source for banned_item in banned)


def test_the_console_binds_the_local_machine_only():
    cli_source = (_SRC / "cli" / "main.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in cli_source


def test_no_module_owns_a_send_path():
    forbidden = ("smtplib", "def draft_send", "def mail_send", "def send_draft")
    for path in sorted(_SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not any(item in text for item in forbidden), f"send-shaped symbol in {path}"
