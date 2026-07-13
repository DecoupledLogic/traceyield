#!/usr/bin/env python3
"""
Tests for E3-F3-S2: externalize the HTML template into a packaged resource
(traceyield/resources/report.html) loaded via importlib.resources.

Covers both acceptance-criteria scenarios of the story:

  AC1 "Packaged resource" -- resources/report.html exists, is non-empty,
      still carries the __PAYLOAD__/__PRICEROWS__ markers, html.HTML_TMPL is
      loaded FROM that resource (not a hardcoded literal), report.HTML_TMPL
      is still the same object (re-export identity preserved), and
      build_html() still substitutes both markers end-to-end.

  AC2 "Wheel carries the HTML" -- CI already runs the whole suite against a
      BUILT AND INSTALLED WHEEL (see .github/workflows/ci.yml: `python -m
      build --wheel` -> `pip install dist/*.whl` -> `python -m unittest
      discover -s tests`), so the AC1 is_file()/read_text() assertions below
      already prove the wheel shipped the resource whenever this suite runs
      in CI. On top of that, this file also builds a wheel directly and
      inspects its namelist as a second, more direct proof -- guarded so it
      is skipped on machines without the `build` module (e.g. this dev
      machine) but runs green in CI where `build` is installed.

Stdlib-only (unittest), imports the installed traceyield package (mirrors
tests/test_report.py's/tests/test_module_split.py's style).
"""
import importlib.resources
import importlib.util
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from traceyield import html, report

# report.py's terse json.load(open(...)) idiom leaks file handles on CPython's
# GC schedule; not relevant here, but silence it for readable test output,
# mirroring tests/test_report.py.
warnings.simplefilter("ignore", ResourceWarning)


# --------------------------------------------------------------- AC1: packaged resource
class TestPackagedResourceAndSubstitution(unittest.TestCase):
    """AC1 evidence: the resource file exists and is well-formed, HTML_TMPL
    is loaded from it (not hardcoded), the report.py re-export identity
    survives, and build_html() still substitutes both markers."""

    def test_resource_file_exists_and_is_nonempty(self):
        res = importlib.resources.files("traceyield.resources").joinpath("report.html")
        self.assertTrue(res.is_file())
        text = res.read_text(encoding="utf-8")
        self.assertTrue(text)
        self.assertIn("__PAYLOAD__", text)
        self.assertIn("__PRICEROWS__", text)

    def test_html_tmpl_loaded_from_resource_not_hardcoded(self):
        res = importlib.resources.files("traceyield.resources").joinpath("report.html")
        resource_text = res.read_text(encoding="utf-8")
        self.assertEqual(html.HTML_TMPL, resource_text)
        self.assertIn("__PAYLOAD__", html.HTML_TMPL)
        self.assertIn("__PRICEROWS__", html.HTML_TMPL)

    def test_report_reexport_identity_preserved(self):
        self.assertIs(report.HTML_TMPL, html.HTML_TMPL)

    def test_build_html_substitutes_both_markers_from_resource_backed_template(self):
        out = html.build_html({}, {}, {}, None)
        self.assertNotIn("__PAYLOAD__", out)
        self.assertNotIn("__PRICEROWS__", out)
        # sanity: still a well-formed HTML document, not an empty/broken string
        self.assertTrue(out.startswith("<!doctype html>"))
        self.assertTrue(out.rstrip().endswith("</html>"))


# --------------------------------------------------------------- AC2: wheel carries the HTML
class TestWheelCarriesHtmlResource(unittest.TestCase):
    """AC2 evidence. The strongest proof is structural: CI
    (.github/workflows/ci.yml) builds a wheel, installs it, and runs this
    entire test suite against that installed wheel -- so
    TestPackagedResourceAndSubstitution above (is_file()/read_text()
    succeeding) already proves the wheel shipped resources/report.html
    whenever this suite runs in CI.

    This test adds a second, direct proof: build a wheel into a temp dir and
    assert the .whl zip's namelist contains the resource. Guarded so it's
    skipped where the `build` module isn't installed (e.g. this dev
    machine) but runs green in CI where `build` is a declared CI-only tool.
    """

    @unittest.skipUnless(
        importlib.util.find_spec("build"), "build module not installed"
    )
    def test_built_wheel_namelist_contains_resource_html(self):
        repo_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                [sys.executable, "-m", "build", "--wheel", "--outdir", tmpdir],
                cwd=str(repo_root),
                check=True,
                capture_output=True,
            )
            wheels = list(Path(tmpdir).glob("*.whl"))
            self.assertEqual(len(wheels), 1, f"expected exactly one wheel, found {wheels}")
            with zipfile.ZipFile(wheels[0]) as zf:
                names = zf.namelist()
            matches = [n for n in names if n.replace("\\", "/").endswith(
                "traceyield/resources/report.html")]
            self.assertTrue(
                matches,
                f"traceyield/resources/report.html not found in wheel namelist: {names}",
            )


if __name__ == "__main__":
    unittest.main()
