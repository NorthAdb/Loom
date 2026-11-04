"""Compatibility shim: the module moved to loom.ops.checkpoints."""

import sys

from loom.ops import checkpoints

sys.modules[__name__] = checkpoints
