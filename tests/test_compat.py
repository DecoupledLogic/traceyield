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


def _src_machine_dir():
    """Ground truth for what the root compat shim's `--machine-dir` prints:
    a FRESH subprocess that mirrors exactly what report.py itself does
    (insert src/ onto sys.path, then `from traceyield import report`), so
    this is independent of whatever traceyield happens to be pip-installed
    (editable or a real wheel) in the *current* environment.

    Before E3-F1-S5, `pkg_report.MACHINE_DIR` (imported once at module scope
    from whatever is pip-installed) was a safe stand-in for this, because
    only an editable install of this exact checkout was ever used in CI/dev.
    Now that CI (and this suite) also runs against a genuinely separate
    built-wheel install, `pkg_report` may resolve to a copy living under
    site-packages with a different HERE/MACHINE_DIR (the installed-package
    data-directory gap tracked by E3-F4-S1 -- see docs/architecture.md,
    "Known gap: installed-package data directory"), while the compat shim
    -- because it forcibly inserts this checkout's src/ at sys.path[0] in a
    fresh process -- always resolves against the local source tree
    regardless. Recomputing ground truth this way keeps these tests valid
    under both install modes.
    """
    src = os.path.join(ROOT, "src")
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); "
         "from traceyield import report; print(report.MACHINE_DIR)" % src],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _traceyield_is_editable_install_of_this_checkout():
    """True when the currently pip-installed `traceyield` resolves to this
    checkout's src/traceyield/__init__.py (an editable/dev install), False
    when it's a separately-built wheel installed elsewhere (e.g. under
    site-packages) -- the scenario E3-F1-S5's CI job now also exercises."""
    installed_at = os.path.normcase(os.path.abspath(pkg_report.__file__))
    checkout_src_report = os.path.normcase(
        os.path.abspath(os.path.join(ROOT, "src", "traceyield", "report.py")))
    return installed_at == checkout_src_report


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
        self.assertEqual(result.stdout.strip(), _src_machine_dir())

    def test_machine_dir_flag_matches_package_module_invocation(self):
        # This equality only holds when the pip-installed traceyield IS this
        # checkout (an editable/dev install): under a real wheel install
        # (E3-F1-S5's CI job), `-m traceyield --machine-dir` resolves under
        # the install location while `report.py --machine-dir` still forces
        # this checkout's src/ tree, so the two legitimately diverge -- the
        # installed-package data-directory gap tracked by E3-F4-S1 (see
        # docs/architecture.md). Skip rather than assert a false equality.
        if not _traceyield_is_editable_install_of_this_checkout():
            self.skipTest(
                "traceyield is installed from a built wheel, not an "
                "editable install of this checkout -- report.py "
                "--machine-dir and `-m traceyield --machine-dir` are "
                "expected to diverge here (known gap, tracked by "
                "E3-F4-S1); see docs/architecture.md."
            )
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
        self.assertEqual(result.stdout.strip(), _src_machine_dir())

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
        self.assertEqual(result.stdout.strip(), _src_machine_dir())


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
