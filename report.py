#!/usr/bin/env python3
"""Legacy root entry point for `python report.py` / `import report`.

Transitional compat shim (E3-F1-S3): report.py moved into src/traceyield/
(E3-F1-S2). This thin wrapper makes src/ importable, then either aliases
itself onto the real traceyield.report module (so `import report` shares
the exact module object/state as `from traceyield import report` -- no
duplicate PRICING table, no divergent MACHINE_DIR) or, when run directly,
dispatches through traceyield.cli:main so `python report.py` behaves
exactly as before the move, including the `--machine-dir` fast path
run.cmd depends on. Removed in E3-F5-S1.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from traceyield import report as _report

if __name__ == "__main__":
    from traceyield import cli
    raise SystemExit(cli.main(["report"] + sys.argv[1:]))

sys.modules[__name__] = _report
