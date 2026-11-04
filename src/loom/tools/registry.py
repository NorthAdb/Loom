"""Compatibility shim: the registry lives in the loom.tools package itself."""

from loom.tools import ToolSpec, build_mail_transport, make_tools

__all__ = ["ToolSpec", "build_mail_transport", "make_tools"]
