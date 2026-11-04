"""Compatibility shim: the module moved to loom.core.graph."""

import sys

from loom.core import graph

sys.modules[__name__] = graph
