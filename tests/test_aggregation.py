#!/usr/bin/env python3
"""
Tests for traceyield.aggregation -- analyze()/aggregate()/top_sessions() (the
bucket builders that turn raw transcript lines or a canonical usage.db into
the day/session/tier aggregates the HTML report renders).

Split out of tests/test_report.py (E3-F3-S3) to mirror the E3-F3-S1 module
boundary. Fixtures moved to tests/helpers.py.

Stdlib-only (unittest) so they run with no extra dependencies:
    python -m unittest discover -s tests   (or: python -m pytest tests -q)

Imports the installed traceyield package (`pip install -e .`), not the repo
root or src/ tree directly, so the suite exercises the same import surface a
consumer of the package would.

The interesting surface is analyze(): it turns raw transcript lines into the
day/session/tier aggregates the HTML report renders, including the two newer
features -- per-session cost accumulation and the per-tier token breakdown the
model-routing estimator consumes. Fixtures are built with hand-computable
numbers so expected costs are checked exactly, not approximately.
"""
import tempfile
import unittest
import warnings

from traceyield import report, canonical

from helpers import (
    assistant, tool_result, prompt, usage, write_transcript, line,
    ingest_and_aggregate, codex_session_meta, codex_turn_context,
    codex_token_count, tok, codex_message,
)

# report.py favors a terse `json.load(open(...))` idiom that leaks file handles
# on CPython's GC schedule; that's a deliberate single-file style choice, not a
# bug the tests should fail on. Keep test output readable.
warnings.simplefilter("ignore", ResourceWarning)


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


# --------------------------------------------------------------- by_provider
class TestAggregateByProvider(unittest.TestCase):
    """aggregate(conn) (provider=None, the new default) must aggregate over
    ALL providers and attach a by_provider facet to each day/session bucket,
    while aggregate(conn, provider='claude') keeps omitting it (the
    equivalence oracle from TestAggregateEquivalence)."""

    def _mixed_conn(self):
        claude_root = tempfile.mkdtemp()
        write_transcript(claude_root, "projX", "conv.jsonl", [
            assistant("2026-07-01T10:00:00Z", "s-claude", "claude-sonnet-5",
                      usage(inp=100, out=50, cr=10, w5m=5, w1h=0),
                      tools=[("t1", "Read")]),
            tool_result("2026-07-01T10:00:01Z", "s-claude", "t1"),
        ])

        codex_root = tempfile.mkdtemp()
        write_transcript(codex_root, "2026", "rollout-x.jsonl", [
            codex_session_meta("2026-07-01T11:00:00Z", "s-codex"),
            codex_turn_context("2026-07-01T11:00:01Z", "gpt-5-codex"),
            codex_message("2026-07-01T11:00:02Z", "user", "hello"),
            codex_token_count("2026-07-01T11:00:03Z", total=tok(inp=1000, out=500)),
        ])

        conn = canonical.open_db(":memory:")
        canonical.ingest(conn, [canonical.ClaudeProvider(root=claude_root),
                                 canonical.CodexProvider(root=codex_root)])
        return conn

    def test_by_provider_facet_present_and_sums_match(self):
        conn = self._mixed_conn()
        days, sessions = report.aggregate(conn)
        conn.close()

        seen_providers = set()
        checked_a_day = False
        for d, D in days.items():
            self.assertIn("by_provider", D)
            if not D["by_provider"]:
                continue
            seen_providers |= set(D["by_provider"].keys())
            self.assertAlmostEqual(sum(bp["cost"] for bp in D["by_provider"].values()), D["cost"])
            self.assertEqual(sum(bp["msgs"] for bp in D["by_provider"].values()), D["msgs"])
            for k in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
                self.assertEqual(sum(bp["tok"][k] for bp in D["by_provider"].values()), D["tok"][k])
            checked_a_day = True
        self.assertTrue(checked_a_day)

        checked_a_session = False
        for sid, S in sessions.items():
            self.assertIn("by_provider", S)
            if not S["by_provider"]:
                continue
            self.assertAlmostEqual(sum(bp["cost"] for bp in S["by_provider"].values()), S["cost"])
            self.assertEqual(sum(bp["msgs"] for bp in S["by_provider"].values()), S["msgs"])
            for k in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
                self.assertEqual(sum(bp["tok"][k] for bp in S["by_provider"].values()), S["tok"][k])
            checked_a_session = True
        self.assertTrue(checked_a_session)

        # guard: the mixed fixture must have actually produced both providers,
        # else the sums-invariant assertions above prove nothing.
        self.assertIn("claude", seen_providers)
        self.assertIn("codex", seen_providers)

    def test_codex_unpriced_but_counted(self):
        conn = self._mixed_conn()
        days, _ = report.aggregate(conn)
        conn.close()

        codex_buckets = [D["by_provider"]["codex"] for D in days.values()
                         if "codex" in D.get("by_provider", {})]
        self.assertTrue(codex_buckets)
        for cb in codex_buckets:
            self.assertEqual(cb["cost"], 0.0)      # codex has no rate card yet
            self.assertGreaterEqual(cb["msgs"], 1)
            self.assertGreater(cb["tok"]["input"], 0)

    def test_scoped_mode_omits_by_provider(self):
        conn = self._mixed_conn()
        days_c, sessions_c = report.aggregate(conn, provider="claude")
        conn.close()

        self.assertIn("2026-07-01", days_c)
        self.assertNotIn("by_provider", days_c["2026-07-01"])
        self.assertIn("s-claude", sessions_c)
        self.assertNotIn("by_provider", sessions_c["s-claude"])


# ----------------------------------------------------------------- by_model
class TestAggregateByModel(unittest.TestCase):
    """aggregate(conn) (provider=None, model=None -- the fully-unscoped call)
    must additionally nest a by_model_full facet: one FULL bucket per model,
    both at the top level (all providers, that model) and inside each
    by_provider[p] (that provider, that model -- the provider x model
    intersection). A concrete `model` (with or without a concrete `provider`)
    takes a scoped branch and stays lean, exactly like provider-only
    scoping (E2-F3-S1)."""

    def _multi_model_conn(self):
        claude_root = tempfile.mkdtemp()
        write_transcript(claude_root, "projX", "conv.jsonl", [
            assistant("2026-07-01T10:00:00Z", "s-claude-1", "claude-sonnet-5",
                      usage(inp=100, out=50, cr=10, w5m=5, w1h=0),
                      tools=[("t1", "Read")]),
            tool_result("2026-07-01T10:00:01Z", "s-claude-1", "t1"),
            assistant("2026-07-01T10:05:00Z", "s-claude-2", "claude-opus-4-8",
                      usage(inp=200, out=80, cr=0, w5m=0, w1h=0),
                      tools=[("t2", "Write")]),
            tool_result("2026-07-01T10:05:01Z", "s-claude-2", "t2"),
        ])

        codex_root = tempfile.mkdtemp()
        write_transcript(codex_root, "2026", "rollout-x.jsonl", [
            codex_session_meta("2026-07-01T11:00:00Z", "s-codex"),
            codex_turn_context("2026-07-01T11:00:01Z", "gpt-5-codex"),
            codex_message("2026-07-01T11:00:02Z", "user", "hello"),
            codex_token_count("2026-07-01T11:00:03Z", total=tok(inp=1000, out=500)),
        ])

        conn = canonical.open_db(":memory:")
        canonical.ingest(conn, [canonical.ClaudeProvider(root=claude_root),
                                 canonical.CodexProvider(root=codex_root)])
        return conn

    def test_by_model_full_present_and_sums_match(self):
        conn = self._multi_model_conn()
        days, sessions = report.aggregate(conn)
        conn.close()

        seen_models = set()
        checked_a_day = False
        for d, D in days.items():
            self.assertIn("by_model_full", D)
            if not D["by_model_full"]:
                continue
            seen_models |= set(D["by_model_full"].keys())
            self.assertAlmostEqual(sum(bm["cost"] for bm in D["by_model_full"].values()), D["cost"])
            self.assertEqual(sum(bm["msgs"] for bm in D["by_model_full"].values()), D["msgs"])
            for k in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
                self.assertEqual(sum(bm["tok"][k] for bm in D["by_model_full"].values()), D["tok"][k])
            checked_a_day = True
        self.assertTrue(checked_a_day)

        checked_a_session = False
        for sid, S in sessions.items():
            self.assertIn("by_model_full", S)
            if not S["by_model_full"]:
                continue
            self.assertAlmostEqual(sum(bm["cost"] for bm in S["by_model_full"].values()), S["cost"])
            self.assertEqual(sum(bm["msgs"] for bm in S["by_model_full"].values()), S["msgs"])
            checked_a_session = True
        self.assertTrue(checked_a_session)

        # guard: the fixture must have actually produced 2+ models, else the
        # sums-invariant assertions above prove nothing.
        self.assertGreaterEqual(len(seen_models), 2)
        self.assertIn("sonnet", seen_models)
        self.assertIn("opus", seen_models)

    def test_provider_model_intersection_reconciles(self):
        """AC2: summing by_provider[p].by_model_full[m].cost over all m equals
        by_provider[p].cost, and summing over all p and m equals D.cost --
        totals reconcile at the provider x model intersection."""
        conn = self._multi_model_conn()
        days, _ = report.aggregate(conn)
        conn.close()

        checked = False
        for d, D in days.items():
            if not D.get("by_provider"):
                continue
            grand_total = 0.0
            for p, bp in D["by_provider"].items():
                self.assertIn("by_model_full", bp)
                if not bp["by_model_full"]:
                    continue
                psum = sum(bm["cost"] for bm in bp["by_model_full"].values())
                self.assertAlmostEqual(psum, bp["cost"])
                grand_total += psum
                checked = True
            self.assertAlmostEqual(grand_total, D["cost"])
        self.assertTrue(checked)

    def test_intersection_matches_direct_scoped_call(self):
        """The nested by_provider[p].by_model_full[m] bucket is literally what
        a direct aggregate(conn, provider=p, model=m) call returns for that
        day -- it's built by reusing that same scoped call, not re-derived."""
        conn = self._multi_model_conn()
        days, _ = report.aggregate(conn)

        found = False
        for d, D in days.items():
            for p, bp in D.get("by_provider", {}).items():
                for m, bucket in bp.get("by_model_full", {}).items():
                    direct_days, _ = report.aggregate(conn, provider=p, model=m)
                    self.assertEqual(bucket, direct_days.get(d))
                    found = True
        conn.close()
        self.assertTrue(found)

    def test_model_scoped_mode_is_lean_and_filters_correctly(self):
        conn = self._multi_model_conn()
        days_sonnet, sessions_sonnet = report.aggregate(conn, model="sonnet")
        conn.close()

        self.assertTrue(days_sonnet)
        for D in days_sonnet.values():
            self.assertNotIn("by_provider", D)
            self.assertNotIn("by_model_full", D)
            # cost/tok/by_model only reflect the 'sonnet' tier
            self.assertEqual(set(D["by_model"].keys()), {"sonnet"})
            # by_tool: only the sonnet turn's tool ("Read") shows up, not the
            # opus turn's ("Write") -- the tool_call<->turn tier join excludes it.
            self.assertIn("Read", D["by_tool"])
            self.assertNotIn("Write", D["by_tool"])
        for S in sessions_sonnet.values():
            self.assertNotIn("by_provider", S)
            self.assertNotIn("by_model_full", S)


if __name__ == "__main__":
    unittest.main()
