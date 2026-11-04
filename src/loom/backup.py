"""Compatibility shim: the module moved to loom.ops.backup.

The shim aliases sys.modules so patches applied to `loom.backup` (the test
seam for datetime) reach the live create_backup and restore_backup.
"""

import sys

from loom.ops import backup

sys.modules[__name__] = backup
