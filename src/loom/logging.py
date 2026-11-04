"""Compatibility shim: the module moved to loom.core.logging."""

import sys

from loom.core import logging

sys.modules[__name__] = logging
