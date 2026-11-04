"""Compatibility shim: the module moved to loom.ops.budget."""

import sys

from loom.ops import budget

sys.modules[__name__] = budget
