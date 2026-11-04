"""Compatibility shim: the module moved to loom.core.llm."""

import sys

from loom.core import llm

sys.modules[__name__] = llm
