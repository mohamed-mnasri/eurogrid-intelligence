"""Test package. Keep test output readable: only warnings and errors are logged unless EUROGRID_LOG_LEVEL is set."""

import os

os.environ.setdefault("EUROGRID_LOG_LEVEL", "WARNING")
