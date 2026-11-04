"""Compatibility shim: the module moved to loom.ops.eval."""

import sys

from loom.ops import eval

sys.modules[__name__] = eval
