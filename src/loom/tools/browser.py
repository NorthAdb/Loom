"""Compatibility shim: the browser module's source stays at loom.browser (the gate tests pin it there)."""

import sys

from loom import browser

sys.modules[__name__] = browser
