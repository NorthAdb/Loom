"""Compatibility shim: the module moved to loom.core.runtime.

The shim aliases sys.modules so patches applied to `loom.runtime` (the test
seam for make_model) reach the live open_graph.
"""

import sys

from loom.core import runtime

sys.modules[__name__] = runtime
