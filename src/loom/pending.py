"""Compatibility shim: the module moved to loom.gateway.pending."""

import sys

from loom.gateway import pending

sys.modules[__name__] = pending
