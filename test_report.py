#!/usr/bin/env python3
"""
Tests for report.py.

Stdlib-only (unittest) so they run with no extra dependencies:
    python -m unittest test_report       (or: python -m pytest test_report.py)

The interesting surface is analyze(): it turns raw transcript lines into the
day/session/tier aggregates the HTML report renders, including the two newer
features — per-session cost accumulation and the per-tier token breakdown the
model-routing estimator consumes. Fixtures are built with hand-computable
numbers so expected costs are checked exactly, not approximately.
"""
import json, os, tempfile, unittest, warnings
import report

# report.py favors a terse `json.load(open(...))` idiom that leaks file handles
# on CPython's GC schedule; that's a deliberate single-file style choice, not a
# bug the tests should fail on. Keep test output readable.
warnings.simplefilter("ignore", ResourceWarning)


# --------------------------------------------------------------- helpers
def line(**kw):
    """One transcript JSON line."""
    return json.dumps(kw)

def assistant(ts, sid, model, usage, tools=()):
    """An assistant message: model + usage, optional tool_use blocks."""
    content = [{"type": "tool_use", "id": tid, "name": name} for tid, name in tools]
    return line(timestamp=ts, sessionId=sid,
                message={"model": model, "usage": usage, "content": content})

def tool_result(ts, sid, tool_use_id, is_error=False, text="ok"):
    """A user message carrying a tool_result block (no usage/model)."""
    return line(timestamp=ts, sessionId=sid,
                message={"content": [{"type": "tool_result",
                                      "tool_use_id": tool_use_id,
                                      "is_error": is_error, "content": text}]})

def usage(inp=0, out=0, cr=0, cc=0, w5m=None, w1h=None):
    u = {"input_tokens": inp, "output_tokens": out,
         "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc}
    if w5m is not None or w1h is not None:
        u["cache_creation"] = {"ephemeral_5m_input_tokens": w5m or 0,
                               "ephemeral_1h_input_tokens": w1h or 0}
    return u

def write_transcript(root, project, name, lines):
    d = os.path.join(root, project)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# --------------------------------------------------------------- pure helpers
class TestPureHelpers(unittest.TestCase):
    def test_tier_mapping(self):
        self.assertEqual(report.tier("claude-opus-4-8"), "opus")
        self.assertEqual(report.tier("claude-fable-5"), "opus")      # fable → opus tier
        self.assertEqual(report.tier("claude-sonnet-5"), "sonnet")
        self.assertEqual(report.tier("claude-haiku-4-5"), "haiku")
        self.assertIsNone(report.tier("gpt-4o"))
        self.assertIsNone(report.tier(None))
        self.assertIsNone(report.tier(""))

    def test_cache_rates(self):
        r = report.cache_rates(5.0)
        self.assertAlmostEqual(r["read"], 0.5)
        self.assertAlmostEqual(r["w5m"], 6.25)
        self.assertAlmostEqual(r["w1h"], 10.0)

    def test_classify_matches_rules(self):
        self.assertEqual(report.classify("Error: file has not been read yet"), "read_before_write")
        self.assertEqual(report.classify("bash: foo: command not found"), "shell_cmd_not_found")
        self.assertEqual(report.classify("String to replace not found in file"), "edit_no_match")
        self.assertEqual(report.classify("no such file or directory"), "file_not_found")
        self.assertEqual(report.classify("InputValidationError: bad param"), "input_validation")

    def test_classify_unknown_is_other(self):
        self.assertEqual(report.classify("something totally unexpected"), "other")

    def test_error_meta_covers_every_rule_plus_other(self):
        for name, *_ in report.ERROR_RULES:
            self.assertIn(name, report.ERROR_META)
            self.assertIn("fix", report.ERROR_META[name])
        self.assertIn("other", report.ERROR_META)

    def test_top_sessions_sorts_caps_and_attaches_id(self):
        sessions = {f"s{i}": {"cost": float(i)} for i in range(5)}
        top = report.top_sessions(sessions, n=3)
        self.assertEqual([s["cost"] for s in top], [4.0, 3.0, 2.0])   # desc
        self.assertEqual(top[0]["id"], "s4")                          # id attached
        self.assertEqual(len(top), 3)                                 # capped


# --------------------------------------------------------------- analyze()
class TestAnalyze(unittest.TestCase):
    """
    Fixture — one project, two sessions, hand-computable costs.

    Opus rates 5/25 per 1M → cache read 0.5, write-5m 6.25, write-1h 10.0.
    Haiku rates 1/5 per 1M → cache write-5m 1.25.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        lines = [
            # s1 spans two days, uses Read then Bash (Bash errors).
            assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4",
                      usage(inp=100, out=50, cr=1000, w5m=200, w1h=300),
                      tools=[("t1", "Read")]),                      # cost 0.0065
            tool_result("2026-01-01T10:00:01Z", "s1", "t1"),        # ok result
            assistant("2026-01-02T09:00:00Z", "s1", "claude-opus-4",
                      usage(inp=10, out=10), tools=[("t2", "Bash")]),  # cost 0.0003
            tool_result("2026-01-02T09:00:01Z", "s1", "t2",
                        is_error=True, text="command not found"),   # shell error
            # s2, one haiku final-response turn; cache_creation fallback (cc→5m).
            assistant("2026-01-02T11:00:00Z", "s2", "claude-haiku-4-5",
                      usage(inp=1000, out=500, cc=400)),            # cost 0.004
            "{ this is not valid json",                             # resilience
            line(message={"model": "x", "usage": usage(inp=9)}),    # no timestamp → skip
        ]
        write_transcript(root, "projX", "conv.jsonl", lines)
        self.days, self.sessions = report.analyze(root)

    def tearDown(self):
        self.tmp.cleanup()

    # ---- day bucketing & cost ----
    def test_days_bucketed_by_activity_date(self):
        self.assertEqual(set(self.days), {"2026-01-01", "2026-01-02"})

    def test_day1_cost_and_tokens(self):
        d = self.days["2026-01-01"]
        self.assertAlmostEqual(d["cost"], 0.0065, places=6)
        self.assertEqual(d["msgs"], 1)
        self.assertEqual(d["tok"], {"input": 100, "output": 50, "cache_read": 1000,
                                    "cache_write_5m": 200, "cache_write_1h": 300})
        self.assertEqual(d["sessions"], 1)

    def test_day2_cost_is_opus_plus_haiku(self):
        d = self.days["2026-01-02"]
        self.assertAlmostEqual(d["cost"], 0.0003 + 0.004, places=6)
        self.assertEqual(d["msgs"], 2)
        self.assertEqual(d["sessions"], 2)

    def test_cache_creation_fallback_to_5m(self):
        # haiku turn gave only aggregate cache_creation=400 → all attributed to 5m.
        d = self.days["2026-01-02"]
        self.assertEqual(d["tok"]["cache_write_5m"], 400)
        self.assertEqual(d["tok"]["cache_write_1h"], 0)

    # ---- per-tier token breakdown (routing estimator depends on this) ----
    def test_by_model_carries_token_breakdown(self):
        opus = self.days["2026-01-01"]["by_model"]["opus"]
        self.assertAlmostEqual(opus["cost"], 0.0065, places=6)
        self.assertEqual(opus["tok"]["cache_write_1h"], 300)
        self.assertEqual(opus["tok"]["input"], 100)

    def test_routing_recost_from_breakdown(self):
        # The estimator recomputes opus tokens at another tier's rates. Replicate
        # that arithmetic here to lock the data contract the JS relies on.
        tok = self.days["2026-01-01"]["by_model"]["opus"]["tok"]
        s_in, s_out = report.PRICING["sonnet"]
        at_sonnet = (tok["input"] * s_in + tok["output"] * s_out
                     + tok["cache_read"] * s_in * 0.1
                     + tok["cache_write_5m"] * s_in * 1.25
                     + tok["cache_write_1h"] * s_in * 2.0) / 1e6
        self.assertAlmostEqual(at_sonnet, 0.0026, places=6)         # < opus 0.0065

    # ---- tools ----
    def test_tool_calls_and_error_attribution(self):
        d2 = self.days["2026-01-02"]["by_tool"]
        self.assertEqual(d2["Bash"]["calls"], 1)
        self.assertEqual(d2["Bash"]["err"], 1)
        # single-tool turn → whole turn cost lands on that tool
        self.assertAlmostEqual(d2["Bash"]["cost"], 0.0003, places=6)

    def test_final_response_pseudo_row(self):
        # haiku turn had no tool_use → cost bucketed under "(final response)"
        self.assertIn("(final response)", self.days["2026-01-02"]["by_tool"])

    def test_tool_results_and_errors_counted(self):
        self.assertEqual(self.days["2026-01-01"]["tool_results"], 1)
        self.assertEqual(self.days["2026-01-02"]["tool_results"], 1)
        self.assertEqual(self.days["2026-01-02"]["tool_errors"], 1)
        self.assertEqual(self.days["2026-01-02"]["errors"], {"shell_cmd_not_found": 1})

    def test_by_project(self):
        self.assertIn("projX", self.days["2026-01-01"]["by_project"])
        self.assertAlmostEqual(self.days["2026-01-01"]["by_project"]["projX"]["cost"], 0.0065, places=6)

    # ---- sessions (per-session cost analysis) ----
    def test_session_accumulates_across_days(self):
        s1 = self.sessions["s1"]
        self.assertAlmostEqual(s1["cost"], 0.0068, places=6)        # 0.0065 + 0.0003
        self.assertEqual(s1["msgs"], 2)
        self.assertEqual(s1["project"], "projX")

    def test_session_span_start_end(self):
        s1 = self.sessions["s1"]
        self.assertEqual(s1["start"], "2026-01-01T10:00:00Z")
        self.assertEqual(s1["end"], "2026-01-02T09:00:01Z")         # last touch = tool_result

    def test_session_tier_mix_and_errors(self):
        s1 = self.sessions["s1"]
        self.assertAlmostEqual(s1["by_model"]["opus"], 0.0068, places=6)
        self.assertEqual(s1["tool_results"], 2)
        self.assertEqual(s1["tool_errors"], 1)

    def test_second_session_isolated(self):
        s2 = self.sessions["s2"]
        self.assertAlmostEqual(s2["cost"], 0.004, places=6)
        self.assertIn("haiku", s2["by_model"])
        self.assertNotIn("opus", s2["by_model"])

    def test_unknown_model_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, "p", "c.jsonl", [
                assistant("2026-02-01T00:00:00Z", "sx", "gpt-4", usage(inp=1000, out=1000)),
            ])
            days, sessions = report.analyze(root)
            # The day/session are still seen (the id is counted), but an
            # unrecognized model tier contributes no cost and no by_model entry.
            self.assertEqual(days["2026-02-01"]["cost"], 0.0)
            self.assertEqual(days["2026-02-01"]["by_model"], {})
            self.assertEqual(sessions["sx"]["cost"], 0.0)
            self.assertEqual(sessions["sx"]["by_model"], {})


# --------------------------------------------------------------- persistence
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

    def test_record_pricing_stamps_today(self):
        import datetime
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "pricing.json")
            hist = report.record_pricing(path=p)
            today = datetime.date.today().isoformat()
            self.assertIn(today, hist)
            self.assertEqual(hist[today]["opus"]["input"], report.PRICING["opus"][0])


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
        html = report.build_html(days, sessions, {"2026-01-01": report.PRICING})
        self.assertNotIn("__PAYLOAD__", html)
        self.assertNotIn("__PRICEROWS__", html)
        # payload embedded and parseable
        blob = html.split("const DATA = ", 1)[1].split(";\nconst META", 1)[0]
        payload = json.loads(blob)
        self.assertIn("sessions", payload)
        self.assertIn("pricing", payload)
        self.assertEqual(payload["sessions"][0]["id"], "s1")
        self.assertEqual(payload["pricing"]["opus"]["input"], report.PRICING["opus"][0])


if __name__ == "__main__":
    unittest.main()
