"""Compatibility shim: the mail module's source stays at loom.mail (the gate tests pin it there)."""

import sys

from loom import mail

sys.modules[__name__] = mail
