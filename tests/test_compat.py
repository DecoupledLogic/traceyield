#!/usr/bin/env python3
"""
Tests for the root compat wrappers report.py / canonical.py (E3-F1-S3).

This is the ONE deliberate, intentional exception to the "no test imports a
module directly from the repo root" rule (E3-F1-S4 AC2, enforced elsewhere by
tests/test_layout.py's static scan): its entire purpose is to prove that
`import report` / `import canonical` at the repo root still resolve, so it
must import the root shims by definition. It is removed together with those
shims in E3-F5-S1 -- at which point the allowlist in test_layout.py shrinks
to zero.

report.py and canonical.py moved into src/traceyield/ in E3-F1-S2, which
broke `python report.py` and `import report` / `import canonical`. These
tests prove the thin root wrappers restore both, and that they do so by
aliasing into the package (same module object/state), never by forking a
second copy of the logic.

Stdlib-only (unittest); no dependency on real ~/.claude data. AC1's "produces
report.html as before" is proven via delegation (the script dispatches into
traceyield.cli:main with the expected argv), not by running the real
pipeline -- that would be slow, machine-dependent, and would pollute real
artifacts.
"""
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

# ROOT is the parent of tests/, i.e. the directory holding the root compat
# shims report.py / canonical.py. It is put on sys.path here -- and only
# here -- so `import report` / `import canonical` below resolve to those
# shims regardless of how the suite was invoked (unittest discovery, pytest,
# or a different cwd). traceyield itself comes from the installed package,
# not from this path.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from traceyield import canonical as pkg_canonical
from traceyield import cli as pkg_cli
from traceyield import report as pkg_report

REPORT_PY = os.path.join(ROOT, "report.py")
CANONICAL_PY = os.path.join(ROOT, "canonical.py")


class TestReportWrapperImport(unittest.TestCase):
    def test_import_report_exposes_expected_symbols(self):
        import report
        for name in ("PRICING", "MACHINE_DIR", "tier", "classify",
                     "result_text", "project_of", "machine_id", "analyze",
                     "merge_daily"):
            self.assertTrue(hasattr(report, name), f"report.{name} missing")

    def test_report_module_is_same_object_as_package_module(self):
        import report
        self.assertIs(report, pkg_report)
        self.assertIs(sys.modules["report"], pkg_report)

    def test_report_prices_share_identity_not_a_copy(self):
        import report
        self.assertIs(report.PRICING, pkg_report.PRICING)
        self.assertIs(report.MACHINE_DIR, pkg_report.MACHINE_DIR)


class TestCanonicalWrapperImport(unittest.TestCase):
    def test_import_canonical_exposes_expected_symbols(self):
        import canonical
        for name in ("SCHEMA_VERSION", "SCHEMA", "open_db", "ingest",
                     "age_out", "write"):
            self.assertTrue(hasattr(canonical, name), f"canonical.{name} missing")

    def test_canonical_module_is_same_object_as_package_module(self):
        import canonical
        self.assertIs(canonical, pkg_canonical)
        self.assertIs(sys.modules["canonical"], pkg_canonical)

    def test_canonical_schema_version_shares_identity_not_a_copy(self):
        import canonical
        self.assertEqual(canonical.SCHEMA_VERSION, pkg_canonical.SCHEMA_VERSION)
        self.assertIs(canonical.SCHEMA, pkg_canonical.SCHEMA)


class TestReportWrapperDelegation(unittest.TestCase):
    """Proves `python report.py` dispatches into traceyield.cli:main rather
    than reimplementing the pipeline, without actually running it."""

    def _run_as_main(self):
        old_argv = sys.argv
        sys.argv = [REPORT_PY]
        try:
            runpy.run_path(REPORT_PY, run_name="__main__")
        finally:
            sys.argv = old_argv

    def test_running_as_script_delegates_to_cli_main_with_report_subcommand(self):
        calls = []

        def fake_main(argv=None):
            calls.append(argv)
            return 0

        with mock.patch.object(pkg_cli, "main", side_effect=fake_main):
            with self.assertRaises(SystemExit) as cm:
                self._run_as_main()
            self.assertEqual(cm.exception.code, 0)

        self.assertEqual(calls, [["report"]])

    def test_running_as_script_propagates_cli_main_nonzero_exit(self):
        with mock.patch.object(pkg_cli, "main", side_effect=lambda argv=None: 3):
            with self.assertRaises(SystemExit) as cm:
                self._run_as_main()
            self.assertEqual(cm.exception.code, 3)


class TestMachineDirSubprocess(unittest.TestCase):
    """`run.cmd` invokes `report.py --machine-dir` as a real subprocess and
    depends on it printing a path and exiting 0 without parsing transcripts;
    exercise the real __main__ path, not an in-process shortcut."""

    def test_machine_dir_flag_prints_resolved_path_and_exits_zero(self):
        result = subprocess.run(
            [sys.executable, REPORT_PY, "--machine-dir"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), pkg_report.MACHINE_DIR)

    def test_machine_dir_flag_matches_package_module_invocation(self):
        # traceyield is installed (editable install), so no PYTHONPATH
        # manipulation is needed for `-m traceyield` to resolve.
        pkg_result = subprocess.run(
            [sys.executable, "-m", "traceyield", "--machine-dir"],
            capture_output=True, text=True, cwd=ROOT,
        )
        wrapper_result = subprocess.run(
            [sys.executable, REPORT_PY, "--machine-dir"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(pkg_result.returncode, 0, pkg_result.stderr)
        self.assertEqual(wrapper_result.returncode, 0, wrapper_result.stderr)
        self.assertEqual(wrapper_result.stdout.strip(), pkg_result.stdout.strip())

    def test_machine_dir_flag_works_regardless_of_cwd(self):
        # The wrapper resolves src/ relative to its own __file__, not cwd, so
        # a scheduled task invoking it from an arbitrary working directory
        # (as run.cmd does) must still find traceyield.
        result = subprocess.run(
            [sys.executable, REPORT_PY, "--machine-dir"],
            capture_output=True, text=True, cwd=tempfile.gettempdir(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), pkg_report.MACHINE_DIR)

    def test_machine_dir_flag_does_not_require_preexisting_pythonpath(self):
        # Fresh subprocess, no PYTHONPATH set: proves the wrapper itself
        # inserts src/ on sys.path rather than relying on an already-active
        # test-runner sys.path.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run(
            [sys.executable, REPORT_PY, "--machine-dir"],
            capture_output=True, text=True, cwd=ROOT, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), pkg_report.MACHINE_DIR)


class TestCanonicalWrapperSubprocess(unittest.TestCase):
    def test_import_canonical_as_script_entry_point_has_no_import_error(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import canonical; "
             "print(canonical.SCHEMA_VERSION)" % ROOT],
            capture_output=True, text=True, cwd=tempfile.gettempdir(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(pkg_canonical.SCHEMA_VERSION))


if __name__ == "__main__":
    unittest.main()
