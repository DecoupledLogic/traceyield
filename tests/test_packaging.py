"""Tests for the pyproject.toml packaging metadata and the minimal
src/traceyield console-entry-point package (E3-F1-S1).

Stdlib-only, no dependency on real ~/.claude data. traceyield.cli is imported
from the installed package (`pip install -e .`); pyproject.toml is read
directly off disk (a config file, not a module import) since it lives at the
repo root regardless of how the package is installed.
"""

import os
import tomllib
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_pyproject():
    path = os.path.join(REPO_ROOT, "pyproject.toml")
    with open(path, "rb") as f:
        return tomllib.load(f)


class TestPyprojectMetadata(unittest.TestCase):
    def test_build_backend_is_setuptools(self):
        data = load_pyproject()
        build_system = data["build-system"]
        self.assertEqual(build_system["build-backend"], "setuptools.build_meta")

    def test_build_requires_mentions_setuptools(self):
        data = load_pyproject()
        requires = data["build-system"]["requires"]
        self.assertTrue(requires, "build-system.requires must be non-empty")
        self.assertTrue(
            any("setuptools" in req for req in requires),
            "build-system.requires must mention setuptools",
        )

    def test_project_name(self):
        data = load_pyproject()
        self.assertEqual(data["project"]["name"], "traceyield")

    def test_console_script_entry_point(self):
        data = load_pyproject()
        scripts = data["project"]["scripts"]
        self.assertEqual(scripts["traceyield"], "traceyield.cli:main")

    def test_no_runtime_dependencies(self):
        data = load_pyproject()
        self.assertEqual(data["project"]["dependencies"], [])


class TestCliEntryPoint(unittest.TestCase):
    def test_main_is_callable(self):
        from traceyield import cli

        self.assertTrue(callable(cli.main))

    def test_main_no_args_returns_zero(self):
        from traceyield import cli

        self.assertEqual(cli.main([]), 0)

    def test_main_help_exits_zero(self):
        from traceyield import cli

        with self.assertRaises(SystemExit) as ctx:
            cli.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_main_version_exits_zero(self):
        from traceyield import cli

        with self.assertRaises(SystemExit) as ctx:
            cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
