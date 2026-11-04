"""Compatibility shim: the calendar module's source stays at loom.calendar so loom.browser imports it without initializing loom.tools."""

import sys

from loom import calendar

sys.modules[__name__] = calendar
