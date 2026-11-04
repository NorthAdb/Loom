"""Compatibility shim: the module moved to loom.tools.sandbox_server."""

import sys

from loom.tools import sandbox_server

sys.modules[__name__] = sandbox_server
