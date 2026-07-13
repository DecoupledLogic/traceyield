#!/usr/bin/env python3
"""Legacy root entry point for `import canonical`.

Transitional compat shim (E3-F1-S3): canonical.py moved into
src/traceyield/ (E3-F1-S2). This thin wrapper makes src/ importable and
aliases itself onto the real traceyield.canonical module, so `import
canonical` shares the exact module object/state as `from traceyield import
canonical` (e.g. `canonical.DB_FILE`, `canonical.SCHEMA_VERSION`). Removed
in E3-F5-S1.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from traceyield import canonical as _canonical

sys.modules[__name__] = _canonical
