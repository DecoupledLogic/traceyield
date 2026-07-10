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
import json, os, re, socket, tempfile, unittest, warnings
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


# --------------------------------------------------------------- machine identity
class TestMachineId(unittest.TestCase):
    """machine_id() picks the per-machine data directory. Hostname by default,
    TOKENLENS_MACHINE override, always sanitized to a filesystem-safe slug."""

    def setUp(self):
        self._saved = os.environ.pop("TOKENLENS_MACHINE", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("TOKENLENS_MACHINE", None)
        else:
            os.environ["TOKENLENS_MACHINE"] = self._saved

    def _expect(self, raw):
        return re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-._") or "unknown"

    def test_defaults_to_sanitized_hostname(self):
        self.assertEqual(report.machine_id(), self._expect(socket.gethostname()))

    def test_env_override_wins_and_is_sanitized(self):
        os.environ["TOKENLENS_MACHINE"] = "Charl's PC!"
        self.assertEqual(report.machine_id(), "charl-s-pc")

    def test_blank_override_falls_back_to_hostname(self):
        os.environ["TOKENLENS_MACHINE"] = "   "
        self.assertEqual(report.machine_id(), self._expect(socket.gethostname()))

    def test_data_files_are_namespaced_under_machine_dir(self):
        # Derived artifacts live under machines/<id>/; pricing_history is shared
        # at the repo root because it comes from PRICING, not from transcripts.
        self.assertEqual(os.path.dirname(report.DAILY_FILE), report.MACHINE_DIR)
        self.assertEqual(os.path.dirname(report.SESSION_FILE), report.MACHINE_DIR)
        self.assertEqual(os.path.dirname(report.OUT_HTML), report.MACHINE_DIR)
        self.assertEqual(os.path.dirname(report.MACHINE_DIR), report.MACHINES_DIR)
        self.assertEqual(os.path.dirname(report.PRICING_FILE), report.HERE)


# --------------------------------------------------------------- pricing drift
# A trimmed fixture mirroring the real Anthropic pricing page: the Model pricing
# table (with a deprecated and a retired row to skip), plus a Batch table below
# it that lists the same tiers at HALF price — the parser must not read that one.
PRICING_PAGE = """\
# Pricing

## Model pricing

| Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes | Cache Hits & Refreshes | Output Tokens |
| ----- | ----------------- | --------------- | --------------- | ---------------------- | ------------- |
| Claude Opus 4.8 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |
| Claude Opus 4.1 ([deprecated](/x)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |
| Claude Sonnet 5 [through August 31, 2026](/y) | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok |
| Claude Sonnet 5 starting September 1, 2026 | $3 / MTok | $3.75 / MTok | $6 / MTok | $0.30 / MTok | $15 / MTok |
| Claude Haiku 4.5 | $1 / MTok | $1.25 / MTok | $2 / MTok | $0.10 / MTok | $5 / MTok |
| Claude Haiku 3.5 ([retired](/z)) | $0.80 / MTok | $1 / MTok | $1.60 / MTok | $0.08 / MTok | $4 / MTok |

## Batch processing

| Model | Batch input | Batch output |
| ----- | ----------- | ------------ |
| Claude Opus 4.8 | $2.50 / MTok | $12.50 / MTok |
| Claude Haiku 4.5 | $0.50 / MTok | $2.50 / MTok |
"""


class TestPricingDrift(unittest.TestCase):
    def test_parse_reads_input_and_output_columns(self):
        p = report.parse_pricing_page(PRICING_PAGE)
        self.assertEqual(p["opus"], (5.0, 25.0))
        self.assertEqual(p["haiku"], (1.0, 5.0))      # not the $0.50 batch row

    def test_parse_takes_first_nondeprecated_row_per_tier(self):
        p = report.parse_pricing_page(PRICING_PAGE)
        self.assertEqual(p["opus"], (5.0, 25.0))      # 4.8, not deprecated 4.1 ($15)
        self.assertEqual(p["sonnet"], (2.0, 10.0))    # intro row, not the Sept-1 $3 row

    def test_parse_ignores_tables_outside_model_pricing(self):
        # The Batch table lists Opus at $2.50/$12.50; the Model pricing value wins.
        self.assertEqual(report.parse_pricing_page(PRICING_PAGE)["opus"], (5.0, 25.0))

    def test_parse_returns_empty_when_section_absent(self):
        self.assertEqual(report.parse_pricing_page("# Pricing\n\nno table here"), {})

    def _drift(self, page):
        orig = report._fetch_pricing_page
        report._fetch_pricing_page = lambda url=None, timeout=15: page
        try:
            return report.check_pricing_drift()
        finally:
            report._fetch_pricing_page = orig

    def _page_from(self, rates):
        """Build a minimal Model pricing table from a {tier: (in, out)} dict."""
        rows = "\n".join(f"| Claude {t.title()} X | ${i} / MTok | ${i*1.25} / MTok "
                         f"| ${i*2} / MTok | ${i*0.1} / MTok | ${o} / MTok |"
                         for t, (i, o) in rates.items())
        return ("## Model pricing\n\n| Model | Base Input | 5m | 1h | hit | Output |\n"
                "| - | - | - | - | - | - |\n" + rows + "\n\n## Batch\n")

    def test_drift_empty_when_pricing_matches_page(self):
        # Generated from the live PRICING dict, so this stays green across
        # legitimate rate edits (e.g. Sonnet intro pricing lapsing).
        self.assertEqual(self._drift(self._page_from(report.PRICING)), [])

    def test_drift_reports_changed_tier(self):
        bumped = dict(report.PRICING)
        oi, oo = bumped["opus"]
        bumped["opus"] = (oi + 1, oo)                       # page says opus costs $1 more
        drift = self._drift(self._page_from(bumped))
        self.assertTrue(any(d.startswith("opus:") for d in drift))
        self.assertFalse(any(d.startswith("haiku:") for d in drift))

    def test_drift_never_raises_on_fetch_failure(self):
        orig = report._fetch_pricing_page
        def boom(url=None, timeout=15): raise OSError("offline")
        report._fetch_pricing_page = boom
        try:
            self.assertEqual(report.check_pricing_drift(), [])   # swallowed, not raised
        finally:
            report._fetch_pricing_page = orig


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
