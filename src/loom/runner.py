"""Compatibility shim: the module moved to loom.core.runner."""

import sys

from loom.core import runner

sys.modules[__name__] = runner
