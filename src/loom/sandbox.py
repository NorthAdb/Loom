"""Compatibility shim: the module moved to loom.tools.sandbox."""

import sys

from loom.tools import sandbox

sys.modules[__name__] = sandbox
