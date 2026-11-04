"""Compatibility shim: the module moved to loom.core.config."""

import sys

from loom.core import config

sys.modules[__name__] = config
