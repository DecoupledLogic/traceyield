#!/usr/bin/env python3
"""
Tests for E3-F3-S1: split report.py into aggregation / persistence / health /
html modules.

Covers both acceptance-criteria scenarios of the story:

  AC1 "Separated concerns" -- the four new modules
      (traceyield.aggregation/.persistence/.health/.html) exist and hold their
      headline symbols; report.py's re-exports of those symbols are the SAME
      objects (identity, not copies) -- proving this is a pure move, not a
      duplicated/forked implementation.

  AC2 "I/O boundary" -- aggregation.aggregate() can be unit-tested against an
      in-memory sqlite connection and runs WITHOUT touching the filesystem or
      network: builtins.open, glob.glob, and urllib.request.urlopen are all
      monkeypatched to raise if called during the aggregate() call, and the
      call still succeeds and returns the (days, sessions) tuple shape.

Stdlib-only (unittest), imports the installed traceyield package (mirrors
tests/test_report.py's/tests/test_models.py's style).
"""
import builtins
import glob as glob_module
import unittest
import urllib.request
import warnings

from traceyield import aggregation, canonical, health, html, persistence, report

# report.py's terse json.load(open(...)) idiom leaks file handles on CPython's
# GC schedule; not relevant here, but silence it for readable test output,
# mirroring tests/test_report.py.
warnings.simplefilter("ignore", ResourceWarning)


# --------------------------------------------------------------- AC1: separated concerns
class TestModulesExistAndHoldHeadlineSymbols(unittest.TestCase):
    """AC1 evidence: the four modules import cleanly and each headline symbol
    lives in its new module."""

    def test_four_modules_import_cleanly(self):
        import traceyield.aggregation
        import traceyield.persistence
        import traceyield.health
        import traceyield.html
        # (import success alone is the assertion; nothing further to check)

    def test_headline_symbols_live_in_their_new_modules(self):
        self.assertTrue(callable(aggregation.aggregate))
        self.assertTrue(callable(aggregation.analyze))
        self.assertTrue(callable(aggregation.top_sessions))
        self.assertTrue(callable(persistence.merge_daily))
        self.assertTrue(callable(persistence.merge_sessions))
        self.assertTrue(callable(persistence.record_pricing))
        self.assertTrue(callable(health.coverage))
        self.assertTrue(callable(health.schema_drift))
        self.assertTrue(callable(health.scan_claude))
        self.assertTrue(callable(health.scan_codex))
        self.assertTrue(callable(html.build_html))
        self.assertIsInstance(html.HTML_TMPL, str)

    def test_html_tmpl_still_contains_both_markers(self):
        self.assertIn("__PAYLOAD__", html.HTML_TMPL)
        self.assertIn("__PRICEROWS__", html.HTML_TMPL)


class TestReportReExportsAreTheSameObjects(unittest.TestCase):
    """AC1 identity/back-compat: report.X re-exports are the SAME objects as
    the new modules' definitions, not copies -- the refactor is a pure move."""

    def test_aggregation_reexports(self):
        self.assertIs(report.aggregate, aggregation.aggregate)
        self.assertIs(report.analyze, aggregation.analyze)
        self.assertIs(report.top_sessions, aggregation.top_sessions)
        self.assertIs(report.new_day, aggregation.new_day)
        self.assertIs(report.new_session, aggregation.new_session)
        self.assertIs(report.new_tool, aggregation.new_tool)
        self.assertIs(report.new_model, aggregation.new_model)
        self.assertIs(report._empty_bucket, aggregation._empty_bucket)

    def test_persistence_reexports(self):
        self.assertIs(report.merge_daily, persistence.merge_daily)
        self.assertIs(report.merge_sessions, persistence.merge_sessions)
        self.assertIs(report.record_pricing, persistence.record_pricing)
        self.assertIs(report.write_health, persistence.write_health)
        self.assertIs(report._norm_pricing_entry, persistence._norm_pricing_entry)

    def test_health_reexports(self):
        self.assertIs(report.scan_claude, health.scan_claude)
        self.assertIs(report.scan_codex, health.scan_codex)
        self.assertIs(report.coverage, health.coverage)
        self.assertIs(report.schema_drift, health.schema_drift)
        self.assertIs(report.build_health, health.build_health)
        self.assertIs(report.print_health, health.print_health)
        self.assertIs(report._fp, health._fp)
        self.assertIs(report._slim_health, health._slim_health)
        self.assertIs(report.SCHEMA_EXPECT, health.SCHEMA_EXPECT)

    def test_html_reexports(self):
        self.assertIs(report.build_html, html.build_html)
        self.assertIs(report.HTML_TMPL, html.HTML_TMPL)


# --------------------------------------------------------------- AC2: I/O boundary
class TestAggregateIsIOFree(unittest.TestCase):
    """AC2: aggregate() operates purely on a passed-in open sqlite
    connection -- no filesystem or network access. Proven by monkeypatching
    builtins.open, glob.glob, and urllib.request.urlopen to raise if invoked
    during the call, then asserting aggregate() still succeeds."""

    def _tiny_canonical_db(self):
        """A minimal, deterministic, synthetic in-memory canonical db: one
        Claude turn (with a tool call) on one day, one session. Built via
        canonical.write(), not raw SQL, so it stays in lockstep with the
        real schema."""
        from traceyield import models
        conn = canonical.open_db(":memory:")
        turn = models.Turn(
            provider="claude", session_id="s1", turn_id="t1",
            ts="2026-01-01T00:00:00Z", model="claude-opus-4-8",
            input_fresh=1000, cache_read=0, cache_write_5m=0,
            cache_write_1h=0, output=100, n_tool_calls=1,
            tier="opus", project="projX",
        )
        call = models.ToolCall(
            provider="claude", session_id="s1", call_id="c1", turn_id="t1",
            ts="2026-01-01T00:00:00Z", name="Read", kind="file_read",
        )
        result = models.ToolCall(
            provider="claude", session_id="s1", call_id="c1", turn_id="t1",
            ts="2026-01-01T00:00:01Z", ok=True,
        )
        sess = models.Session(
            provider="claude", session_id="s1", project="projX",
            first_ts="2026-01-01T00:00:00Z", last_ts="2026-01-01T00:00:01Z",
        )
        for rec in (turn, call, result, sess):
            canonical.write(conn, rec, verbatim=True)
        conn.commit()
        return conn

    def test_aggregate_returns_days_sessions_tuple_shape_without_real_io(self):
        conn = self._tiny_canonical_db()

        real_open = builtins.open
        real_glob = glob_module.glob
        real_urlopen = urllib.request.urlopen

        def _boom_open(*a, **kw):
            raise AssertionError("aggregate() touched the filesystem via open()")

        def _boom_glob(*a, **kw):
            raise AssertionError("aggregate() touched the filesystem via glob.glob()")

        def _boom_urlopen(*a, **kw):
            raise AssertionError("aggregate() touched the network via urlopen()")

        builtins.open = _boom_open
        glob_module.glob = _boom_glob
        urllib.request.urlopen = _boom_urlopen
        try:
            days, sessions = aggregation.aggregate(conn, provider="claude")
        finally:
            builtins.open = real_open
            glob_module.glob = real_glob
            urllib.request.urlopen = real_urlopen
            conn.close()

        self.assertIsInstance(days, dict)
        self.assertIsInstance(sessions, dict)
        self.assertIn("2026-01-01", days)
        self.assertIn("s1", sessions)
        self.assertEqual(days["2026-01-01"]["msgs"], 1)
        self.assertEqual(sessions["s1"]["project"], "projX")

    def test_unscoped_aggregate_also_survives_the_io_guards(self):
        # provider=None, model=None takes the by_provider/by_model_full facet
        # branch (recursive scoped calls) -- exercise that path under the same
        # I/O guards to prove the recursion doesn't sneak in file/network access.
        conn = self._tiny_canonical_db()

        real_open = builtins.open
        real_glob = glob_module.glob
        real_urlopen = urllib.request.urlopen
        builtins.open = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("touched filesystem via open()"))
        glob_module.glob = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("touched filesystem via glob.glob()"))
        urllib.request.urlopen = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("touched network via urlopen()"))
        try:
            days, sessions = aggregation.aggregate(conn)
        finally:
            builtins.open = real_open
            glob_module.glob = real_glob
            urllib.request.urlopen = real_urlopen
            conn.close()

        self.assertIsInstance(days, dict)
        self.assertIsInstance(sessions, dict)
        self.assertIn("by_provider", days["2026-01-01"])
        self.assertIn("claude", days["2026-01-01"]["by_provider"])


if __name__ == "__main__":
    unittest.main()
