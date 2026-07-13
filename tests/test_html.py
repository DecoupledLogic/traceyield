#!/usr/bin/env python3
"""
Tests for traceyield.html -- build_html() and the packaged HTML_TMPL
resource (the report's HTML/CSS/JS shell and its payload substitution).

Split out of tests/test_report.py (E3-F3-S3) to mirror the E3-F3-S1 module
boundary; folds in the E3-F3-S2 packaged-resource tests (formerly
tests/test_html_resource.py) so the html boundary lives in one file.
Fixtures moved to tests/helpers.py.

Stdlib-only (unittest) so they run with no extra dependencies:
    python -m unittest discover -s tests   (or: python -m pytest tests -q)

Imports the installed traceyield package (`pip install -e .`), not the repo
root or src/ tree directly, so the suite exercises the same import surface a
consumer of the package would.
"""
import importlib.resources
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from traceyield import html, report

# report.py's terse json.load(open(...)) idiom leaks file handles on CPython's
# GC schedule; that's a deliberate single-file style choice, not a bug the
# tests should fail on. Keep test output readable.
warnings.simplefilter("ignore", ResourceWarning)


# --------------------------------------------------------------- build_html
class TestBuildHtml(unittest.TestCase):
    def test_placeholders_filled_and_payload_valid(self):
        days = {"2026-01-01": {"cost": 1.0, "by_model": {}, "by_project": {},
                               "by_tool": {}, "errors": {},
                               "tok": {"input": 0, "output": 0, "cache_read": 0,
                                       "cache_write_5m": 0, "cache_write_1h": 0},
                               "msgs": 0, "tool_results": 0, "tool_errors": 0, "sessions": 0}}
        sessions = {"s1": {"cost": 2.0, "tok": {}, "msgs": 1, "tool_results": 0,
                           "tool_errors": 0, "project": "p", "start": "2026-01-01T00:00:00Z",
                           "end": "2026-01-01T00:00:00Z", "by_model": {"opus": 2.0}}}
        html_out = report.build_html(days, sessions, {"2026-01-01": report.PRICING})
        self.assertNotIn("__PAYLOAD__", html_out)
        self.assertNotIn("__PRICEROWS__", html_out)
        # payload embedded and parseable
        blob = html_out.split("const DATA = ", 1)[1].split(";\nconst META", 1)[0]
        payload = json.loads(blob)
        self.assertIn("sessions", payload)
        self.assertIn("pricing", payload)
        self.assertEqual(payload["sessions"][0]["id"], "s1")
        self.assertEqual(payload["pricing"]["opus"]["input"], report.PRICING["opus"][0])

    def test_provider_filter_and_byprovider_panel_present(self):
        """Cost-by-provider panel (bar) + provider filter control markers
        (E2-F3-S1). There's no JS test harness, so we assert on the emitted
        template string per project convention."""
        days = {"2026-01-01": {"cost": 1.0, "by_model": {}, "by_project": {},
                               "by_tool": {}, "errors": {},
                               "tok": {"input": 0, "output": 0, "cache_read": 0,
                                       "cache_write_5m": 0, "cache_write_1h": 0},
                               "msgs": 0, "tool_results": 0, "tool_errors": 0, "sessions": 0}}
        html_out = report.build_html(days, {}, {"2026-01-01": report.PRICING})
        self.assertIn('id="provider"', html_out)
        self.assertIn('id="byprovider"', html_out)
        self.assertIn("Cost by provider", html_out)

    def test_token_neutral_currency_panel_and_projection_label(self):
        """Tokens-by-provider neutral-currency comparison panel + a projection
        label on the combined cost-by-provider dollar figures (E2-F3-S2).
        There's no JS test harness, so we assert on the emitted template
        string per project convention."""
        days = {"2026-01-01": {"cost": 1.0, "by_model": {}, "by_project": {},
                               "by_tool": {}, "errors": {},
                               "tok": {"input": 0, "output": 0, "cache_read": 0,
                                       "cache_write_5m": 0, "cache_write_1h": 0},
                               "msgs": 0, "tool_results": 0, "tool_errors": 0, "sessions": 0}}
        html_out = report.build_html(days, {}, {"2026-01-01": report.PRICING})
        self.assertIn('id="tokprovider"', html_out)
        self.assertIn("per-provider rate cards", html_out)

    def test_model_slicer_control_present(self):
        """Model selector, unified with the provider filter (E2-F3-S3).
        There's no JS test harness, so we assert on the emitted template
        string per project convention."""
        days = {"2026-01-01": {"cost": 1.0, "by_model": {}, "by_project": {},
                               "by_tool": {}, "errors": {},
                               "tok": {"input": 0, "output": 0, "cache_read": 0,
                                       "cache_write_5m": 0, "cache_write_1h": 0},
                               "msgs": 0, "tool_results": 0, "tool_errors": 0, "sessions": 0}}
        html_out = report.build_html(days, {}, {"2026-01-01": report.PRICING})
        self.assertIn('id="model"', html_out)
        self.assertIn('id="modellbl"', html_out)
        self.assertIn('id="providerlbl"', html_out)


class TestNeutralBranding(unittest.TestCase):
    """Report copy must be provider-neutral in GLOBAL framing (E2-F5-S1); copy that
    describes genuinely Claude-specific mechanics may remain as long as it's
    explicitly attributed to Claude. Minimal empty inputs are fine since the
    template copy under test is static."""

    def test_header_is_neutral(self):
        html_out = report.build_html({}, {}, {}, None)
        self.assertIn("LLM Usage &amp; Health", html_out)
        self.assertNotIn("Claude Code Usage", html_out)

    def test_global_copy_generalized(self):
        html_out = report.build_html({}, {}, {}, None)
        self.assertNotIn("One Claude Code conversation", html_out)
        self.assertIn("One assistant conversation", html_out)
        self.assertNotIn("used Claude Code since", html_out)
        self.assertIn("used a coding assistant since", html_out)

    def test_attributed_claude_copy_preserved(self):
        html_out = report.build_html({}, {}, {}, None)
        self.assertIn("Claude Code serializes tool calls", html_out)


class TestNoEmDashes(unittest.TestCase):
    """E2-F5-S2: the generated report must contain no em dashes -- neither the
    literal U+2014 character nor the &mdash; HTML entity. HTML_TMPL is a static
    string, so an empty-data build already exercises every line of copy; a
    populated-data build additionally exercises the data-conditional health
    branches (stale/hole strings) that are only reachable with real health data."""

    def test_no_em_dashes_empty_data(self):
        html_out = report.build_html({}, {}, {}, None)
        self.assertNotIn("—", html_out)
        self.assertNotIn("&mdash;", html_out)

    def test_no_em_dashes_with_health_data(self):
        days = {"2026-01-01": {"cost": 1.0, "by_model": {}, "by_project": {},
                               "by_tool": {}, "errors": {},
                               "tok": {"input": 0, "output": 0, "cache_read": 0,
                                       "cache_write_5m": 0, "cache_write_1h": 0},
                               "msgs": 0, "tool_results": 0, "tool_errors": 0, "sessions": 0}}
        # Force the "stale" and "suspicious hole" health strings to render by
        # asking for coverage against a "today" far past the last active date.
        cov = report.coverage({"2026-01-01": {"cost": 1.0, "tool_results": 0}}, {}, "2026-02-01")
        health = {"generated": "2026-02-01T00:00:00", "machine": "test",
                  "providers": {"claude": {"scan": {"files": 1, "lines": 1, "json_errors": 0,
                                "seen": {}, "dates": {}, "flags": {"unexpected_new_thing": 1},
                                "unknown_models": []},
                                "drift": ["unexpected_new_thing"], "coverage": cov},
                                "codex": {"scan": report._fp(0, 0, 0, {}, {}, __import__("collections").Counter(), set()), "drift": []}}}
        html_out = report.build_html(days, {}, {"2026-01-01": report.PRICING}, health)
        self.assertNotIn("—", html_out)
        self.assertNotIn("&mdash;", html_out)

    def test_former_prose_em_dash_sites_now_read_cleanly(self):
        # Representative former-prose em-dash sites: assert their replacement
        # punctuation renders and the old &mdash; is gone from that context.
        html_out = report.build_html({}, {}, {}, None)
        self.assertIn("Full turn cost attributed to its single tool", html_out)
        self.assertIn("Claude Code serializes tool calls", html_out)
        self.assertIn("Upper bound", html_out)
        self.assertIn("keep quality-sensitive work on Opus", html_out)

    def test_former_placeholder_em_dash_sites_use_hyphen(self):
        # Table-cell / JS placeholder sites formerly rendered a lone em dash
        # for "no value"; they must now use a plain hyphen with the same
        # semantics (still present verbatim in the static JS source).
        html_out = report.build_html({}, {}, {}, None)
        self.assertIn('v.calls?fmtInt(v.calls):"-"', html_out)
        self.assertIn('s.tool_results?er.toFixed(1)+"%":"-"', html_out)


# --------------------------------------------------------------- AC1 (E3-F3-S2): packaged resource
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


# --------------------------------------------------------------- AC2 (E3-F3-S2): wheel carries the HTML
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
