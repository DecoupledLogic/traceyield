#!/usr/bin/env python3
"""
Tests for traceyield.persistence -- merge_daily()/merge_sessions()/
record_pricing() and the durable-store merge semantics they implement.

Split out of tests/test_report.py (E3-F3-S3) to mirror the E3-F3-S1 module
boundary. Fixtures moved to tests/helpers.py.

Stdlib-only (unittest) so they run with no extra dependencies:
    python -m unittest discover -s tests   (or: python -m pytest tests -q)

Imports the installed traceyield package (`pip install -e .`), not the repo
root or src/ tree directly, so the suite exercises the same import surface a
consumer of the package would.
"""
import json
import os
import tempfile
import unittest
import warnings

from traceyield import report

# report.py's terse json.load(open(...)) idiom leaks file handles on CPython's
# GC schedule; that's a deliberate single-file style choice, not a bug the
# tests should fail on. Keep test output readable.
warnings.simplefilter("ignore", ResourceWarning)


class TestPersistence(unittest.TestCase):
    def test_merge_daily_new_authoritative_old_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "daily.json")
            report.merge_daily({"2026-01-01": {"cost": 1.0}}, path=p)
            merged = report.merge_daily({"2026-01-01": {"cost": 2.0},   # overwrite
                                         "2026-01-02": {"cost": 3.0}}, path=p)
            self.assertEqual(merged["2026-01-01"]["cost"], 2.0)         # new wins
            self.assertEqual(merged["2026-01-02"]["cost"], 3.0)         # added
            # persisted to disk
            with open(p, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["2026-01-01"]["cost"], 2.0)

    def test_merge_daily_tolerates_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "daily.json")
            open(p, "w").write("{corrupt")
            merged = report.merge_daily({"2026-01-01": {"cost": 1.0}}, path=p)
            self.assertEqual(merged, {"2026-01-01": {"cost": 1.0}})

    def test_merge_sessions_keeps_rotated_out(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sessions.json")
            report.merge_sessions({"s1": {"cost": 5.0}}, path=p)
            merged = report.merge_sessions({"s2": {"cost": 9.0}}, path=p)
            self.assertIn("s1", merged)   # old session survives even if not re-parsed
            self.assertIn("s2", merged)

    def test_record_pricing_stamps_today_provider_nested(self):
        import datetime
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "pricing.json")
            hist = report.record_pricing(path=p)
            today = datetime.date.today().isoformat()
            self.assertIn(today, hist)
            entry = hist[today]
            self.assertIn("claude", entry)
            self.assertIn("codex", entry)
            self.assertEqual(entry["claude"]["opus"], {"input": 5.0, "output": 25.0})
            self.assertEqual(entry["codex"]["gpt-5.3-codex"], {"input": 1.75, "output": 14.0})

    def test_record_pricing_migrates_legacy_flat_entries(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "pricing.json")
            legacy = {"2026-01-01": {"opus": {"input": 5.0, "output": 25.0},
                                      "sonnet": {"input": 3.0, "output": 15.0}}}
            json.dump(legacy, open(p, "w", encoding="utf-8"))
            hist = report.record_pricing(path=p)
            self.assertIn("claude", hist["2026-01-01"])
            self.assertEqual(hist["2026-01-01"]["claude"]["opus"], {"input": 5.0, "output": 25.0})
            self.assertEqual(hist["2026-01-01"]["claude"]["sonnet"], {"input": 3.0, "output": 15.0})
            # values survive the migration unchanged, and file re-reads cleanly
            reloaded = json.load(open(p, encoding="utf-8"))
            self.assertEqual(reloaded["2026-01-01"]["claude"]["opus"]["input"], 5.0)

    def test_norm_pricing_entry_upgrades_legacy_flat(self):
        flat = {"opus": {"input": 5.0, "output": 25.0}, "sonnet": {"input": 3.0, "output": 15.0}}
        normalized = report._norm_pricing_entry(flat)
        self.assertEqual(normalized, {"claude": flat})

    def test_norm_pricing_entry_leaves_nested_unchanged(self):
        nested = {"claude": {"opus": {"input": 5.0, "output": 25.0}},
                  "codex": {"gpt-5.3-codex": {"input": 1.75, "output": 14.0}}}
        normalized = report._norm_pricing_entry(nested)
        self.assertEqual(normalized, nested)


if __name__ == "__main__":
    unittest.main()
