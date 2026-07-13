#!/usr/bin/env python3
"""
Tests for traceyield.health -- scan_claude()/scan_codex(), schema_drift(),
coverage(), and build_health()/print_health() (the schema-drift and
coverage-hole monitoring the "Data health" panel is built from).

Split out of tests/test_report.py (E3-F3-S3) to mirror the E3-F3-S1 module
boundary. Fixtures moved to tests/helpers.py.

Stdlib-only (unittest) so they run with no extra dependencies:
    python -m unittest discover -s tests   (or: python -m pytest tests -q)

Imports the installed traceyield package (`pip install -e .`), not the repo
root or src/ tree directly, so the suite exercises the same import surface a
consumer of the package would.
"""
import contextlib
import io
import json
import tempfile
import unittest
import warnings

from traceyield import report

from helpers import assistant, tool_result, usage, write_transcript, codex_file, cx

# report.py's terse json.load(open(...)) idiom leaks file handles on CPython's
# GC schedule; that's a deliberate single-file style choice, not a bug the
# tests should fail on. Keep test output readable.
warnings.simplefilter("ignore", ResourceWarning)


# --------------------------------------------------------------- schema drift & coverage
class TestSchemaScanClaude(unittest.TestCase):
    """scan_claude() fingerprints SHAPE (not cost); schema_drift() flags anything
    outside the SCHEMA_EXPECT baseline — new usage keys, unmapped models."""

    def test_clean_data_has_no_drift(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, "p", "c.jsonl", [
                assistant("2026-03-01T00:00:00Z", "s1", "claude-opus-4-8",
                          usage(inp=10, out=5, cr=100, w5m=1, w1h=2), tools=[("t1", "Read")]),
                tool_result("2026-03-01T00:00:01Z", "s1", "t1"),
            ])
            fp = report.scan_claude(root)
            self.assertEqual(report.schema_drift(fp, "claude"), [])
            self.assertEqual(fp["dates"], {"2026-03-01": 2})   # both lines are dated

    def test_new_usage_key_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            u = usage(inp=10, out=5); u["frobnicate_tokens"] = 7        # unheard-of field
            write_transcript(root, "p", "c.jsonl", [
                assistant("2026-03-01T00:00:00Z", "s1", "claude-opus-4-8", u)])
            drift = report.schema_drift(report.scan_claude(root), "claude")
            self.assertTrue(any("frobnicate_tokens" in d and "usage_key" in d for d in drift))

    def test_unmapped_model_flagged_and_counted(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, "p", "c.jsonl", [
                assistant("2026-03-01T00:00:00Z", "s1", "claude-neptune-9", usage(inp=10, out=5))])
            fp = report.scan_claude(root)
            self.assertIn("claude-neptune-9", fp["unknown_models"])
            self.assertEqual(fp["flags"]["unmapped_model_turns"], 1)
            self.assertTrue(any("unmapped model" in d for d in report.schema_drift(fp, "claude")))

    def test_known_synthetic_model_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, "p", "c.jsonl", [
                assistant("2026-03-01T00:00:00Z", "s1", "<synthetic>", usage(inp=1))])
            fp = report.scan_claude(root)
            self.assertEqual(fp["unknown_models"], [])                  # baseline-known
            self.assertEqual(report.schema_drift(fp, "claude"), [])

    def test_missing_required_key_flagged(self):
        # A fingerprint that never saw 'sessionId' → likely rename → parser blind.
        fp = {"lines": 5, "seen": {"line_keys": ["timestamp", "message"]}, "unknown_models": []}
        drift = report.schema_drift(fp, "claude")
        self.assertTrue(any("sessionId" in d and "required key" in d for d in drift))


class TestSchemaScanCodex(unittest.TestCase):
    """scan_codex() baselines the rollout format before a parser exists and flags
    sessions with activity but no token_count (the $0 'codex exec' gotcha)."""

    def _rollout(self, ts_day, with_tokens=True, model="gpt-5-codex", extra=None):
        lines = [
            cx(timestamp=f"{ts_day}T00:00:00Z", type="session_meta", payload={"id": "sid", "cwd": "/p"}),
            cx(timestamp=f"{ts_day}T00:00:01Z", type="turn_context", payload={"model": model}),
            cx(timestamp=f"{ts_day}T00:00:02Z", type="response_item",
               payload={"type": "function_call", "name": "shell", "call_id": "c1"}),
        ]
        if with_tokens:
            lines.append(cx(timestamp=f"{ts_day}T00:00:03Z", type="event_msg",
                payload={"type": "token_count", "info": {"last_token_usage": {"input_tokens": 10},
                         "total_token_usage": {}, "model_context_window": 272000}}))
        if extra: lines.append(extra)
        return lines

    def test_clean_codex_no_drift(self):
        with tempfile.TemporaryDirectory() as root:
            codex_file(root, "r1.jsonl", self._rollout("2026-03-01"))
            fp = report.scan_codex(root)
            self.assertEqual(report.schema_drift(fp, "codex"), [])
            self.assertIn("gpt-5-codex", fp["seen"]["models"])

    def test_session_without_token_count_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            codex_file(root, "r1.jsonl", self._rollout("2026-03-01", with_tokens=True))
            codex_file(root, "r2.jsonl", self._rollout("2026-03-02", with_tokens=False))
            fp = report.scan_codex(root)
            self.assertEqual(fp["flags"]["files_with_activity"], 2)
            self.assertEqual(fp["flags"]["files_without_usage"], 1)   # only r2

    def test_new_payload_type_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            odd = cx(timestamp="2026-03-01T00:00:05Z", type="response_item",
                     payload={"type": "quantum_entanglement_event"})
            codex_file(root, "r1.jsonl", self._rollout("2026-03-01", extra=odd))
            drift = report.schema_drift(report.scan_codex(root), "codex")
            self.assertTrue(any("quantum_entanglement_event" in d for d in drift))

    def test_non_gpt_model_flagged_unmapped(self):
        with tempfile.TemporaryDirectory() as root:
            codex_file(root, "r1.jsonl", self._rollout("2026-03-01", model="o3-mini"))
            fp = report.scan_codex(root)
            self.assertIn("o3-mini", fp["unknown_models"])


class TestCoverage(unittest.TestCase):
    """coverage() separates benign idle days from suspicious holes and tracks
    staleness — all relative to a fixed 'today' so the test is deterministic."""

    def _day(self, cost=1.0, tool_results=0, msgs=1):
        return {"cost": cost, "tool_results": tool_results, "msgs": msgs}

    def test_calendar_gap_listed(self):
        days = {"2026-01-01": self._day(), "2026-01-03": self._day()}
        cov = report.coverage(days, scan_dates={}, today="2026-01-03")
        self.assertEqual(cov["calendar_gaps"], ["2026-01-02"])
        self.assertEqual(cov["suspicious"], [])                    # no transcript corroboration

    def test_corroborated_hole_is_suspicious(self):
        days = {"2026-01-01": self._day(), "2026-01-03": self._day()}
        cov = report.coverage(days, scan_dates={"2026-01-02": 12}, today="2026-01-03")
        self.assertIn("2026-01-02", cov["calendar_gaps"])
        self.assertEqual([s["date"] for s in cov["suspicious"]], ["2026-01-02"])

    def test_zero_cost_with_activity_is_suspicious(self):
        days = {"2026-01-01": self._day(cost=0.0, tool_results=5)}
        cov = report.coverage(days, scan_dates={}, today="2026-01-01")
        self.assertEqual([s["date"] for s in cov["suspicious"]], ["2026-01-01"])

    def test_days_since_last_active(self):
        days = {"2026-01-01": self._day()}
        cov = report.coverage(days, scan_dates={}, today="2026-01-05")
        self.assertEqual(cov["days_since_last_active"], 4)

    def test_empty_store_is_safe(self):
        cov = report.coverage({}, scan_dates={}, today="2026-01-05")
        self.assertEqual(cov["active_days"], 0)
        self.assertEqual(cov["suspicious"], [])

    def test_zero_cost_suspicious_default_true(self):
        # Default behavior (Claude): a $0-cost day with tool activity IS flagged.
        days = {"2026-01-01": self._day(cost=0.0, tool_results=5)}
        cov = report.coverage(days, scan_dates={}, today="2026-01-01")
        self.assertEqual([s["date"] for s in cov["suspicious"]], ["2026-01-01"])

    def test_zero_cost_suspicious_suppressed_for_codex(self):
        # Codex: recognized-but-unpriced tiers legitimately cost $0, so the
        # $0-cost-with-activity heuristic must be suppressed when asked.
        days = {"2026-01-01": self._day(cost=0.0, tool_results=5)}
        cov = report.coverage(days, scan_dates={}, today="2026-01-01",
                               zero_cost_suspicious=False)
        self.assertEqual(cov["suspicious"], [])

    def test_codex_calendar_hole_still_fires_with_suppression(self):
        # The calendar-gap check (transcripts cover a date the store never
        # recorded) must still fire even with zero_cost_suspicious=False.
        days = {"2026-01-01": self._day(), "2026-01-03": self._day()}
        cov = report.coverage(days, scan_dates={"2026-01-02": 12}, today="2026-01-03",
                               zero_cost_suspicious=False)
        self.assertIn("2026-01-02", cov["calendar_gaps"])
        self.assertEqual([s["date"] for s in cov["suspicious"]], ["2026-01-02"])
        self.assertIn("transcript lines exist", cov["suspicious"][0]["reason"])


class TestHealthRecord(unittest.TestCase):
    def test_build_and_slim(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, "p", "c.jsonl", [
                assistant("2026-03-01T00:00:00Z", "s1", "claude-opus-4-8", usage(inp=10, out=5))])
            fp = report.scan_claude(root)
            days = {"2026-03-01": {"cost": 1.0, "tool_results": 0, "msgs": 1}}
            h = report.build_health(days, fp, report._fp(0, 0, 0, {}, {}, __import__("collections").Counter(), set()))
            self.assertIn("claude", h["providers"])
            self.assertIn("coverage", h["providers"]["claude"])
            slim = report._slim_health(h)
            # slimming drops the big per-date map + churny top-level keys, keeps drift
            self.assertNotIn("dates", slim["providers"]["claude"]["scan"])
            self.assertNotIn("line_keys", slim["providers"]["claude"]["scan"]["seen"])
            self.assertIn("dates", h["providers"]["claude"]["scan"])   # original untouched

    def test_build_html_embeds_health(self):
        days = {"2026-01-01": {"cost": 1.0, "by_model": {}, "by_project": {},
                               "by_tool": {}, "errors": {},
                               "tok": {"input": 0, "output": 0, "cache_read": 0,
                                       "cache_write_5m": 0, "cache_write_1h": 0},
                               "msgs": 0, "tool_results": 0, "tool_errors": 0, "sessions": 0}}
        health = {"generated": "2026-01-01T00:00:00", "machine": "test",
                  "providers": {"claude": {"scan": {"files": 1, "lines": 1, "json_errors": 0,
                                "seen": {}, "dates": {}, "flags": {}, "unknown_models": []},
                                "drift": [], "coverage": report.coverage(
                                    {"2026-01-01": {"cost": 1.0, "tool_results": 0}}, {}, "2026-01-01")},
                                "codex": {"scan": report._fp(0, 0, 0, {}, {}, __import__("collections").Counter(), set()), "drift": []}}}
        html = report.build_html(days, {}, {"2026-01-01": report.PRICING}, health)
        blob = html.split("const DATA = ", 1)[1].split(";\nconst META", 1)[0]
        payload = json.loads(blob)
        self.assertIsNotNone(payload["health"])
        self.assertIn("claude", payload["health"]["providers"])

    def test_build_html_health_optional(self):
        # Back-compat: build_html still works with the old 3-arg call (health=None).
        html = report.build_html({}, {}, {"2026-01-01": report.PRICING})
        blob = html.split("const DATA = ", 1)[1].split(";\nconst META", 1)[0]
        self.assertIsNone(json.loads(blob)["health"])

    def test_build_health_codex_coverage_present_and_hole_flagged(self):
        # AC scenario 1: a codex transcript date with no recorded codex usage
        # is flagged as a suspicious hole in the codex coverage block, while a
        # codex $0-cost-with-activity day is NOT flagged (zero_cost suppression).
        days = {
            "2026-04-01": {
                "cost": 1.0, "tool_results": 0,
                "by_provider": {"codex": {"cost": 0.0, "tool_results": 5}},
            },
        }
        codex_fp = report._fp(0, 0, 0, {}, {"2026-04-02": 3},
                               __import__("collections").Counter(), set())
        claude_fp = report._fp(0, 0, 0, {}, {}, __import__("collections").Counter(), set())
        h = report.build_health(days, claude_fp, codex_fp)
        cov = h["providers"]["codex"]["coverage"]
        self.assertIn("2026-04-02", cov["calendar_gaps"])
        self.assertEqual([s["date"] for s in cov["suspicious"]], ["2026-04-02"])
        # the codex $0-cost day is not flagged
        self.assertNotIn("2026-04-01", [s["date"] for s in cov["suspicious"]])

    def test_build_health_both_providers_have_coverage(self):
        # AC scenario 2: both providers carry a coverage block.
        days = {"2026-04-01": {"cost": 1.0, "tool_results": 0,
                                "by_provider": {"codex": {"cost": 0.5, "tool_results": 1}}}}
        claude_fp = report._fp(0, 0, 0, {}, {}, __import__("collections").Counter(), set())
        codex_fp = report._fp(0, 0, 0, {}, {}, __import__("collections").Counter(), set())
        h = report.build_health(days, claude_fp, codex_fp)
        self.assertIsInstance(h["providers"]["claude"]["coverage"], dict)
        self.assertIsInstance(h["providers"]["codex"]["coverage"], dict)

    def test_build_health_codex_coverage_absent_by_provider_is_empty(self):
        # analyze() fallback path / claude-only machine: no by_provider facet
        # anywhere -- codex coverage should degrade to a benign empty record.
        days = {"2026-04-01": {"cost": 1.0, "tool_results": 0}}
        claude_fp = report._fp(0, 0, 0, {}, {}, __import__("collections").Counter(), set())
        codex_fp = report._fp(0, 0, 0, {}, {}, __import__("collections").Counter(), set())
        h = report.build_health(days, claude_fp, codex_fp)
        self.assertEqual(h["providers"]["codex"]["coverage"]["active_days"], 0)

    def test_print_health_reports_codex_hole(self):
        days = {
            "2026-04-01": {
                "cost": 1.0, "tool_results": 0,
                "by_provider": {"codex": {"cost": 0.0, "tool_results": 5}},
            },
        }
        codex_fp = report._fp(0, 0, 0, {}, {"2026-04-02": 3},
                               __import__("collections").Counter(), set())
        claude_fp = report._fp(0, 0, 0, {}, {}, __import__("collections").Counter(), set())
        h = report.build_health(days, claude_fp, codex_fp)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            report.print_health(h)
        out = buf.getvalue()
        self.assertIn("[codex]", out)
        self.assertIn("2026-04-02", out)


if __name__ == "__main__":
    unittest.main()
