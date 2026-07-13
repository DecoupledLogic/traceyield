#!/usr/bin/env python3
"""
Enforces E3-F1-S4 AC2: "no test imports a module directly from the repo
root". Statically scans every *.py file under tests/ with the ast module and
asserts none of them `import report` / `import canonical` (or `from report
import ...` / `from canonical import ...`) -- those are the root compat
shims (report.py, canonical.py), not the traceyield package.

The single, deliberate, explicitly-named exception is tests/test_compat.py:
its entire purpose is to exercise those root shims, so it must import them.
When the shims are removed in E3-F5-S1, test_compat.py is removed with them
and ALLOWED_ROOT_IMPORTERS below shrinks to empty -- that shrinking is the
visible signal this allowlist is temporary, not a permanent carve-out.
"""
import ast
import os
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

# The forbidden root-level module names: the transitional compat shims at
# the repo root (report.py / canonical.py), NOT the traceyield.report /
# traceyield.canonical package modules, which are fine to import anywhere.
FORBIDDEN_ROOT_MODULES = {"report", "canonical"}

# The one test file allowed to import the root shims, because it is the
# test suite FOR those shims. Named explicitly so the allowlist is visible
# and reviewable, not implicit.
ALLOWED_ROOT_IMPORTERS = {"test_compat.py"}


def _iter_test_files():
    for name in sorted(os.listdir(TESTS_DIR)):
        if name.startswith("test_") and name.endswith(".py"):
            yield name


def _root_module_imports(file_path):
    """Return the set of forbidden root module names imported by the given
    file, found via `import X` or `from X import ...` at any depth in the
    module's AST (so a root import can't be laundered inside a function or
    a try/except block either)."""
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=file_path)

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                if top_level in FORBIDDEN_ROOT_MODULES:
                    found.add(top_level)
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (node.level > 0) can't reach the repo root
            # from inside the tests/ package; only absolute `from X import`
            # can name a root module.
            if node.level == 0 and node.module:
                top_level = node.module.split(".")[0]
                if top_level in FORBIDDEN_ROOT_MODULES:
                    found.add(top_level)
    return found


class TestNoRootImportLeakage(unittest.TestCase):
    """E3-F1-S4 AC2: the src layout means tests import the installed
    traceyield package, never a module straight off the repo root."""

    def test_no_test_file_imports_root_modules_except_the_allowlisted_compat_test(self):
        violations = {}
        for name in _iter_test_files():
            if name in ALLOWED_ROOT_IMPORTERS:
                continue
            file_path = os.path.join(TESTS_DIR, name)
            hits = _root_module_imports(file_path)
            if hits:
                violations[name] = sorted(hits)

        self.assertEqual(
            violations, {},
            "test file(s) import repo-root modules directly (only "
            f"{sorted(ALLOWED_ROOT_IMPORTERS)} is allowed to, as the "
            "dedicated test suite for the root compat shims): "
            f"{violations}",
        )

    def test_allowlisted_compat_test_still_exists_and_still_imports_root_modules(self):
        # Guards against the allowlist going stale in the other direction:
        # if test_compat.py is renamed/removed without updating
        # ALLOWED_ROOT_IMPORTERS, or stops importing the shims it exists to
        # test, that's a bug in this test file, not a passing suite.
        for name in ALLOWED_ROOT_IMPORTERS:
            file_path = os.path.join(TESTS_DIR, name)
            self.assertTrue(
                os.path.isfile(file_path),
                f"allowlisted file {name} does not exist under tests/",
            )
            hits = _root_module_imports(file_path)
            self.assertEqual(
                hits, FORBIDDEN_ROOT_MODULES,
                f"{name} is allowlisted to import the root compat shims "
                "but no longer imports both report and canonical -- update "
                "the allowlist or this test if that's now intentional",
            )

    def test_forbidden_root_module_detection_catches_from_import(self):
        # Self-test of the scanner: `from report import PRICING` must be
        # detected just as `import report` is.
        import tempfile
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, dir=TESTS_DIR,
        ) as f:
            f.write("from report import PRICING\n")
            tmp_path = f.name
        try:
            hits = _root_module_imports(tmp_path)
            self.assertEqual(hits, {"report"})
        finally:
            os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
