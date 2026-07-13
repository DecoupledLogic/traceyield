"""CLI smoke tests for the installed traceyield package (E3-F1-S5).

Companion evidence to the CI job's own smoke steps (`.github/workflows/ci.yml`):
these run the same three commands CI does, but locally, via subprocess, so the
smoke coverage is not CI-only. Each is invoked as
``sys.executable -m traceyield ...`` (rather than shelling out to the
``traceyield`` console script) so the suite is hermetic under both an
editable install and a built-wheel install without depending on PATH.

Hermetic: no real ``~/.claude``/``~/.codex`` parsing (``--help`` and
``--machine-dir`` never touch transcripts; bare invocation just prints a
hint and returns -- see traceyield.cli.main), no writes outside a temp dir
used only to isolate HOME/USERPROFILE so a developer's real machine
artifacts are never touched.
"""

import os
import subprocess
import sys
import tempfile
import unittest


def _run(args, env=None):
    """Run `python -m traceyield <args>` as a subprocess and return the
    completed process (never raises on non-zero exit; callers assert)."""
    return subprocess.run(
        [sys.executable, "-m", "traceyield", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _isolated_env(tmp_dir):
    """A copy of the current environment with HOME/USERPROFILE redirected
    into a throwaway temp dir, so --machine-dir resolution (which is
    home-independent today, but defensively isolated here anyway) can never
    touch a developer's real machine data."""
    env = dict(os.environ)
    env["HOME"] = tmp_dir
    env["USERPROFILE"] = tmp_dir
    return env


class TestCliSmokeHelp(unittest.TestCase):
    def test_help_exits_zero_and_mentions_prog_name(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = _run(["--help"], env=_isolated_env(tmp_dir))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("traceyield", result.stdout)


class TestCliSmokeMachineDir(unittest.TestCase):
    def test_machine_dir_exits_zero_and_prints_single_nonempty_path_line(self):
        # NOTE: deliberately does NOT assert the printed path points at any
        # particular location (e.g. a repo root). Under a wheel install,
        # report.py's HERE anchor resolves somewhere inside the install/venv,
        # not a repo checkout -- that is a known gap tracked by E3-F4-S1
        # (see docs/architecture.md "Persistence & files: installed-package
        # gap"). This test only proves the command is well-behaved: exit 0,
        # exactly one line of output, and that line is non-empty.
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = _run(["--machine-dir"], env=_isolated_env(tmp_dir))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, msg=f"expected one line, got: {lines!r}")
        self.assertTrue(lines[0].strip(), msg="printed path must be non-empty")

    def test_machine_dir_does_not_create_any_directory(self):
        # --machine-dir only resolves and prints a path; it must not create
        # machines/<id> or any other directory as a side effect (creation
        # happens later, only when the report pipeline actually runs).
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _isolated_env(tmp_dir)
            before = set(os.listdir(tmp_dir))
            result = _run(["--machine-dir"], env=env)
            after = set(os.listdir(tmp_dir))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(before, after)


class TestCliSmokeBareInvocation(unittest.TestCase):
    def test_bare_module_invocation_exits_zero_and_prints_hint(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = _run([], env=_isolated_env(tmp_dir))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("traceyield", result.stdout)


if __name__ == "__main__":
    unittest.main()
