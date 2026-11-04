"""Compatibility shim: the module moved to loom.ops.routines."""

import sys

from loom.ops import routines

sys.modules[__name__] = routines
