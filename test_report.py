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
import contextlib, io, json, os, re, socket, tempfile, unittest, warnings
import report, canonical

# report.py favors a terse `json.load(open(...))` idiom that leaks file handles
# on CPython's GC schedule; that's a deliberate single-file style choice, not a
# bug the tests should fail on. Keep test output readable.
warnings.simplefilter("ignore", ResourceWarning)


# --------------------------------------------------------------- helpers
def line(**kw):
    """One transcript JSON line."""
    return json.dumps(kw)

def assistant(ts, sid, model, usage, tools=(), uuid=None):
    """An assistant message: model + usage, optional tool_use blocks, optional
    uuid (Claude Code's turn id -- set it to exercise replay dedup)."""
    content = [{"type": "tool_use", "id": tid, "name": name} for tid, name in tools]
    o = {"timestamp": ts, "sessionId": sid,
         "message": {"model": model, "usage": usage, "content": content}}
    if uuid: o["uuid"] = uuid
    return line(**o)

def tool_result(ts, sid, tool_use_id, is_error=False, text="ok"):
    """A user message carrying a tool_result block (no usage/model)."""
    return line(timestamp=ts, sessionId=sid,
                message={"content": [{"type": "tool_result",
                                      "tool_use_id": tool_use_id,
                                      "is_error": is_error, "content": text}]})

def prompt(ts, sid, text):
    """A plain user prompt line: no usage, no tool_result -- not a billable
    turn or tool touch, so it must not move a session's span or count as a
    day-active-session in either analyze() or aggregate()."""
    return line(timestamp=ts, sessionId=sid, message={"role": "user", "content": text})

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

def ingest_and_aggregate(root):
    """Ingest a transcript root into an in-memory canonical db, then run
    report.aggregate() over it — the equivalence-test twin of report.analyze(root)."""
    conn = canonical.open_db(":memory:")
    canonical.ingest(conn, [canonical.ClaudeProvider(root=root)])
    days, sessions = report.aggregate(conn)
    conn.close()
    return days, sessions


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
    TRACEYIELD_MACHINE override, always sanitized to a filesystem-safe slug."""

    def setUp(self):
        self._saved = os.environ.pop("TRACEYIELD_MACHINE", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("TRACEYIELD_MACHINE", None)
        else:
            os.environ["TRACEYIELD_MACHINE"] = self._saved

    def _expect(self, raw):
        return re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-._") or "unknown"

    def test_defaults_to_sanitized_hostname(self):
        self.assertEqual(report.machine_id(), self._expect(socket.gethostname()))

    def test_env_override_wins_and_is_sanitized(self):
        os.environ["TRACEYIELD_MACHINE"] = "Charl's PC!"
        self.assertEqual(report.machine_id(), "charl-s-pc")

    def test_blank_override_falls_back_to_hostname(self):
        os.environ["TRACEYIELD_MACHINE"] = "   "
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
        # check_pricing_drift() prints its findings to stdout; that's intended in a
        # real run, but here the "drift" is synthetic fixture data (e.g. opus 5.0+1
        # = 6.0), so swallow the output to keep the suite's stdout clean and avoid a
        # scary-looking "Anthropic=6.0" line that isn't a real rate.
        orig = report._fetch_pricing_page
        report._fetch_pricing_page = lambda url=None, timeout=15: page
        try:
            with contextlib.redirect_stdout(io.StringIO()):
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


# --------------------------------------------------------------- aggregate() equivalence
class TestAggregateEquivalence(unittest.TestCase):
    """aggregate() (SQL GROUP BY over the canonical db) must reproduce analyze()'s
    (days, sessions) output exactly, on the same transcripts, within the cost
    rounding both already apply. This is the regression guard for the E1-F2-S1
    "aggregate flip": SQLite becomes the source of truth for the aggregates
    without changing what the report shows."""

    def _check(self, root):
        days_a, sess_a = report.analyze(root)
        days_b, sess_b = ingest_and_aggregate(root)
        self.assertEqual(days_a, days_b)
        self.assertEqual(sess_a, sess_b)

    def test_equivalence_on_the_full_TestAnalyze_fixture(self):
        # Same fixture as TestAnalyze.setUp: two sessions in one file (one
        # spanning two days), opus + haiku, a tool error, cache_creation->5m
        # fallback, a malformed-JSON line, and a no-timestamp line.
        with tempfile.TemporaryDirectory() as root:
            lines = [
                assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4",
                          usage(inp=100, out=50, cr=1000, w5m=200, w1h=300),
                          tools=[("t1", "Read")]),
                tool_result("2026-01-01T10:00:01Z", "s1", "t1"),
                assistant("2026-01-02T09:00:00Z", "s1", "claude-opus-4",
                          usage(inp=10, out=10), tools=[("t2", "Bash")]),
                tool_result("2026-01-02T09:00:01Z", "s1", "t2",
                            is_error=True, text="command not found"),
                assistant("2026-01-02T11:00:00Z", "s2", "claude-haiku-4-5",
                          usage(inp=1000, out=500, cc=400)),
                "{ this is not valid json",
                line(message={"model": "x", "usage": usage(inp=9)}),
            ]
            write_transcript(root, "projX", "conv.jsonl", lines)
            self._check(root)

    def test_equivalence_unknown_model(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, "p", "c.jsonl", [
                assistant("2026-02-01T00:00:00Z", "sx", "gpt-4", usage(inp=1000, out=1000)),
            ])
            self._check(root)

    def test_equivalence_multi_tool_turn(self):
        # A turn with >1 tool_use block must land in the "(multi-tool turn)"
        # pseudo-row in both analyze() and aggregate() -- exercises the last
        # untested branch of the by_tool attribution rule.
        with tempfile.TemporaryDirectory() as root:
            lines = [
                assistant("2026-03-01T00:00:00Z", "sm", "claude-sonnet-5",
                          usage(inp=200, out=100, cr=50, w5m=10, w1h=0),
                          tools=[("m1", "Read"), ("m2", "Grep")]),
                tool_result("2026-03-01T00:00:01Z", "sm", "m1"),
                tool_result("2026-03-01T00:00:02Z", "sm", "m2"),
            ]
            write_transcript(root, "projM", "multi.jsonl", lines)
            days, sessions = report.analyze(root)
            self.assertIn("(multi-tool turn)", days["2026-03-01"]["by_tool"])
            self._check(root)

    def test_equivalence_cross_file_turn_replay_dedup(self):
        # Claude Code replays the SAME assistant turn (same uuid) -- and its
        # tool result -- into a second transcript file on session resume/
        # compaction. Both paths must dedup by uuid/tool_use_id and count it
        # exactly once (the operator's decision: each turn is billed once).
        with tempfile.TemporaryDirectory() as root:
            turn = assistant("2026-04-01T00:00:00Z", "sd", "claude-opus-4",
                             usage(inp=100, out=50, cr=10, w5m=5, w1h=0),
                             tools=[("dtool", "Read")], uuid="turn-dup-1")
            result = tool_result("2026-04-01T00:00:01Z", "sd", "dtool")
            write_transcript(root, "projD", "conv1.jsonl", [turn, result])
            write_transcript(root, "projD", "conv2.jsonl", [turn, result])   # exact replay
            days, sessions = report.analyze(root)
            self.assertEqual(days["2026-04-01"]["msgs"], 1)                  # billed once
            self.assertEqual(days["2026-04-01"]["by_tool"]["Read"]["calls"], 1)
            self.assertEqual(days["2026-04-01"]["tool_results"], 1)
            self.assertEqual(sessions["sd"]["msgs"], 1)
            self._check(root)

    def test_equivalence_leading_prompt_only_line_excluded(self):
        # A plain prompt-only user line, timestamped BEFORE any billable turn
        # or tool touch, must not move the session's start and must not
        # fabricate a day-active-session on the day it alone occupies -- in
        # neither analyze() nor aggregate() (session/day activity is defined
        # over billable-turn + tool_result touches only, in both paths).
        with tempfile.TemporaryDirectory() as root:
            lines = [
                prompt("2026-04-29T23:00:00Z", "sp", "hello"),   # earlier day, no billable turn
                assistant("2026-04-30T00:05:00Z", "sp", "claude-sonnet-5",
                         usage(inp=10, out=5), uuid="turn-p1"),
            ]
            write_transcript(root, "projP", "conv.jsonl", lines)
            days, sessions = report.analyze(root)
            self.assertNotIn("2026-04-29", days)                            # no phantom day
            self.assertEqual(sessions["sp"]["start"], "2026-04-30T00:05:00Z")
            self._check(root)

    def test_equivalence_tool_call_and_result_straddle_midnight(self):
        # A tool_use call at 23:59:30Z and its tool_result the NEXT day
        # (00:00:30Z) -- by_tool[*].calls must land on the CALL's (turn's)
        # day; tool_results/tool_errors/errors/by_tool[*].err must land on the
        # RESULT's day, identically in analyze() and aggregate(). This is the
        # scenario tool_call.ts = MAX(call_ts, result_ts) can drift a day
        # ahead of the call's own turn -- aggregate() must bucket `calls` via
        # the linked turn's day, not tool_call.ts's day, to match analyze().
        with tempfile.TemporaryDirectory() as root:
            lines = [
                assistant("2026-05-10T23:59:30Z", "sn", "claude-opus-4",
                         usage(inp=40, out=20, cr=5, w5m=2, w1h=0),
                         tools=[("n1", "AskUserQuestion")], uuid="turn-mid-1"),
                tool_result("2026-05-11T00:00:30Z", "sn", "n1",
                           is_error=True, text="no such file"),
            ]
            write_transcript(root, "projN", "midnight.jsonl", lines)
            days, sessions = report.analyze(root)
            # the call counts on the call/turn day (2026-05-10)...
            self.assertEqual(days["2026-05-10"]["by_tool"]["AskUserQuestion"]["calls"], 1)
            self.assertEqual(days["2026-05-10"]["by_tool"]["AskUserQuestion"]["err"], 0)
            # ...but the error/tool_results/tool_errors count on the RESULT day (2026-05-11)
            self.assertEqual(days["2026-05-11"]["tool_results"], 1)
            self.assertEqual(days["2026-05-11"]["tool_errors"], 1)
            self.assertEqual(days["2026-05-11"]["errors"], {"file_not_found": 1})
            self.assertEqual(days["2026-05-11"]["by_tool"]["AskUserQuestion"]["err"], 1)
            self.assertEqual(days["2026-05-11"]["by_tool"]["AskUserQuestion"]["calls"], 0)
            self._check(root)

    def test_equivalence_session_project_first_wins_across_files(self):
        # The SAME session_id has turns in TWO project directories across two
        # files (e.g. a worktree switch mid-session -- canonical.py's
        # motivating case for the Session-upsert first-wins fix). Both paths
        # must resolve the session's own `project` field to the FIRST-seen
        # project ("projA"), not whichever file ingest()/analyze() happens to
        # process last -- AND, since `turn.project` is now a per-turn column
        # (not derived from the session's single resolved project), the day
        # `by_project` breakdown must still split cost per-file exactly like
        # analyze() does (projA gets turn-w1's cost, projB gets turn-w2's).
        # Full deep-equality on both days and sessions.
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, "projA", "a.jsonl", [
                assistant("2026-06-01T00:00:00Z", "sw", "claude-opus-4",
                         usage(inp=10, out=5), uuid="turn-w1"),
            ])
            write_transcript(root, "projB", "b.jsonl", [
                assistant("2026-06-01T01:00:00Z", "sw", "claude-opus-4",
                         usage(inp=20, out=10), uuid="turn-w2"),
            ])
            days_a, sess_a = report.analyze(root)
            self.assertEqual(sess_a["sw"]["project"], "projA")            # first-wins
            self.assertIn("projA", days_a["2026-06-01"]["by_project"])    # per-file split preserved
            self.assertIn("projB", days_a["2026-06-01"]["by_project"])
            self._check(root)


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


# --------------------------------------------------------------- schema drift & coverage
def codex_file(root, name, lines):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, name), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def cx(**kw):
    return json.dumps(kw)


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


if __name__ == "__main__":
    unittest.main()
