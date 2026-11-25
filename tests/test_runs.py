#!/usr/bin/env python3
"""Tests for traceyield.runs: one run (a session, its subagents and optional
Codex sessions) extracted as lanes, model turns and tool calls.

Every fixture is synthetic and lives in a temp dir; nothing here reads the
real ~/.claude or ~/.codex. Token counts are hand-computable so costs are
asserted exactly.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from traceyield import cli, models, runs
from traceyield.providers import ClaudeProvider, CodexProvider

from helpers import (
    assistant, codex_custom_tool_call, codex_custom_tool_call_output, codex_file,
    codex_function_call, codex_function_call_output, codex_message, codex_session_meta,
    codex_token_count, codex_turn_context, line, prompt, tok, tool_result, usage,
    write_transcript,
)

SID = "sess-1"
OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
START, END = "2026-11-24T10:00:00Z", "2026-11-24T11:00:00Z"
DENIED = "Permission for this action was denied by the auto mode classifier."


def at(minute, second=0):
    return f"2026-11-24T10:{minute:02d}:{second:02d}.000Z"


class RunFixture(unittest.TestCase):
    """A Claude root and a Codex root in temp dirs, plus extract() bound to them."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.claude_root = os.path.join(self._tmp.name, "claude")
        self.codex_root = os.path.join(self._tmp.name, "codex")
        os.makedirs(self.claude_root)
        os.makedirs(self.codex_root)

    def main_thread(self, lines, sid=SID):
        write_transcript(self.claude_root, "projX", sid + ".jsonl", lines)

    def subagent(self, agent_id, lines, meta=None, sid=SID):
        sub = os.path.join("projX", sid, "subagents")
        write_transcript(self.claude_root, sub, f"agent-{agent_id}.jsonl", lines)
        if meta is not None:
            with open(os.path.join(self.claude_root, sub, f"agent-{agent_id}.meta.json"), "w", encoding="utf-8") as fh:
                json.dump(meta, fh)

    def extract(self, session=SID, start=START, end=END, **kw):
        providers = {"claude": ClaudeProvider(root=self.claude_root), "codex": CodexProvider(root=self.codex_root)}
        return runs.extract(session, start, end, providers=providers, **kw)


class TestTurnsArePricedOncePerRequest(RunFixture):
    def test_records_repeating_one_requests_usage_merge_into_one_turn(self):
        # Claude Code logs one record per content block; each repeats the usage.
        u = usage(inp=1000, out=2000, cr=100000)
        self.main_thread([
            assistant(at(1), SID, OPUS, u, texts=["Checking the repo."], uuid="a1", request_id="req-1"),
            assistant(at(1, 1), SID, OPUS, u, tools=[("t1", "Bash", {"command": "git status"})], uuid="a2", request_id="req-1"),
            tool_result(at(1, 3), SID, "t1"),
        ])
        doc = self.extract()
        self.assertEqual(len(doc["turns"]), 1)
        # opus 5/25 per 1M: 1000*5 + 2000*25 + 100000*0.5 = 105000 / 1e6
        self.assertAlmostEqual(doc["turns"][0]["cost"], 0.105)
        self.assertAlmostEqual(doc["turns"][0]["costRead"], 0.05)
        self.assertEqual(doc["lanes"][0]["cost"], 0.1)   # lane cost is rounded to cents
        self.assertEqual(doc["lanes"][0]["tok"], {"in": 1000, "out": 2000, "read": 100000, "write": 0})

    def test_the_merged_turn_keeps_what_the_model_said_and_its_calls(self):
        u = usage(out=10)
        self.main_thread([
            assistant(at(1), SID, OPUS, u, texts=["Checking the repo."], uuid="a1", request_id="req-1"),
            assistant(at(1, 1), SID, OPUS, u, tools=[("t1", "Bash", {"command": "git status"})], uuid="a2", request_id="req-1"),
            tool_result(at(1, 3), SID, "t1"),
        ])
        doc = self.extract()
        self.assertEqual(doc["turns"][0]["text"], "Checking the repo.")
        self.assertEqual(doc["calls"][0]["turn"], doc["turns"][0]["id"])

    def test_turns_without_a_request_id_stay_separate(self):
        self.main_thread([
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1"),
            assistant(at(2), SID, OPUS, usage(out=10), uuid="a2"),
        ])
        self.assertEqual(len(self.extract()["turns"]), 2)

    def test_a_turns_cost_is_shared_evenly_by_the_calls_it_made(self):
        self.main_thread([
            assistant(at(1), SID, OPUS, usage(out=4000), uuid="a1", request_id="r",
                      tools=[("t1", "Read", {"file_path": "/x/a.py"}), ("t2", "Read", {"file_path": "/x/b.py"})]),
            tool_result(at(1, 1), SID, "t1"), tool_result(at(1, 2), SID, "t2"),
        ])
        calls = self.extract()["calls"]
        self.assertEqual([c["cost"] for c in calls], [0.05, 0.05])
        self.assertEqual([c["turnCalls"] for c in calls], [2, 2])


class TestLanes(RunFixture):
    def test_main_thread_and_subagents_are_separate_lanes_with_model_and_effort(self):
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1", effort="medium")])
        self.subagent("abc", [assistant(at(2), SID, SONNET, usage(out=10), uuid="b1", effort="high")],
                      meta={"agentType": "dev-agent", "description": "TDD rebuild"})
        lanes = self.extract()["lanes"]
        self.assertEqual([(l["id"], l["name"], l["kind"]) for l in lanes],
                         [("main", "Main thread", "main"), ("abc", "dev-agent", "subagent")])
        self.assertEqual([(l["models"], l["efforts"]) for l in lanes],
                         [([OPUS], ["medium"]), ([SONNET], ["high"])])
        self.assertEqual(lanes[1]["description"], "TDD rebuild")
        self.assertNotIn("path", lanes[0])   # local file paths stay out of the document

    def test_a_subagent_without_metadata_is_named_by_its_id(self):
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1")])
        self.subagent("xyz", [assistant(at(2), SID, OPUS, usage(out=10), uuid="b1")])
        self.assertEqual(self.extract()["lanes"][1]["name"], "xyz")

    def test_a_lane_with_nothing_in_the_window_is_left_out(self):
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1")])
        self.subagent("late", [assistant("2026-11-24T12:30:00.000Z", SID, OPUS, usage(out=10), uuid="b1")])
        self.assertEqual([l["id"] for l in self.extract()["lanes"]], ["main"])

    def test_an_unknown_session_has_no_lanes(self):
        doc = self.extract(session="nope")
        self.assertEqual((doc["lanes"], doc["turns"], doc["calls"]), ([], [], []))

    def test_a_malformed_line_does_not_stop_the_lane(self):
        self.main_thread(["{not json", assistant(at(1), SID, OPUS, usage(out=10), uuid="a1"), line(type="mystery")])
        self.assertEqual(len(self.extract()["turns"]), 1)


class TestWindow(RunFixture):
    def test_turns_and_calls_outside_the_window_are_dropped(self):
        self.main_thread([
            assistant("2026-11-24T09:59:59.000Z", SID, OPUS, usage(out=10), uuid="a0",
                      tools=[("t0", "Bash", {"command": "early"})]),
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1", tools=[("t1", "Bash", {"command": "inside"})]),
            assistant("2026-11-24T11:00:01.000Z", SID, OPUS, usage(out=10), uuid="a2"),
        ])
        doc = self.extract()
        self.assertEqual(len(doc["turns"]), 1)
        self.assertEqual([c["input"] for c in doc["calls"]], ["inside"])

    def test_a_fractional_second_past_the_start_is_inside(self):
        # "10:00:00.500Z" sorts before "10:00:00Z" as text; it must still count.
        self.main_thread([assistant("2026-11-24T10:00:00.500Z", SID, OPUS, usage(out=10), uuid="a1")])
        self.assertEqual(len(self.extract()["turns"]), 1)


class TestToolCalls(RunFixture):
    def lines(self):
        return [
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1", request_id="r1",
                      tools=[("t1", "Bash", {"command": "dotnet test\n--no-build", "description": "Run the tests"})]),
            tool_result(at(3, 30), SID, "t1", text="Passed!"),
            assistant(at(4), SID, OPUS, usage(out=10), uuid="a2", request_id="r2",
                      tools=[("t2", "Bash", {"command": "grep nothing file"})]),
            tool_result(at(4, 1), SID, "t2", is_error=True, text="Exit code 1"),
            assistant(at(5), SID, OPUS, usage(out=10), uuid="a3", request_id="r3",
                      tools=[("t3", "Bash", {"command": "taskkill //F //IM dotnet.exe"})]),
            tool_result(at(5, 1), SID, "t3", is_error=True, text=DENIED),
            assistant(at(6), SID, OPUS, usage(out=10), uuid="a4", request_id="r4",
                      tools=[("t4", "Read", {"file_path": "C:\\repo\\src\\Client.cs"})]),
        ]

    def test_a_call_carries_its_duration_summary_input_and_output(self):
        self.main_thread(self.lines())
        c = self.extract()["calls"][0]
        self.assertEqual((c["n"], c["tool"], c["lane"], c["ms"]), (1, "Bash", "main", 150000))
        self.assertEqual((c["summary"], c["input"], c["output"]), ("Run the tests", "dotnet test\n--no-build", "Passed!"))
        self.assertEqual((c["error"], c["denied"], c["background"]), (False, False, False))

    def test_a_failed_call_is_an_error_and_a_refused_one_is_denied(self):
        self.main_thread(self.lines())
        calls = self.extract()["calls"]
        self.assertEqual([(c["error"], c["denied"]) for c in calls[1:3]], [(True, False), (True, True)])
        lane = self.extract()["lanes"][0]
        self.assertEqual((lane["errors"], lane["denied"]), (1, 1))   # a refusal is not counted as an error

    def test_failed_calls_are_grouped_by_error_class_with_the_suggested_fix(self):
        lines = self.lines() + [
            assistant(at(7), SID, OPUS, usage(out=10), uuid="a5", request_id="r5",
                      tools=[("t5", "Bash", {"command": "echo 'oops"})]),
            tool_result(at(7, 1), SID, "t5", is_error=True, text="bash: unexpected EOF while looking for matching quote"),
            assistant(at(8), SID, OPUS, usage(out=10), uuid="a6", request_id="r6",
                      tools=[("t6", "Bash", {"command": "echo 'again"})]),
            tool_result(at(8, 1), SID, "t6", is_error=True, text="bash: unexpected EOF while looking for matching quote"),
        ]
        self.main_thread(lines)
        errors = self.extract()["errors"]
        self.assertEqual([(e["class"], e["calls"], e["denied"]) for e in errors],
                         [("shell_syntax", [5, 6], False), ("other", [2], False), ("user_rejected", [3], True)])
        self.assertEqual(errors[0]["title"], "Shell quoting / path-escaping error")
        self.assertTrue(errors[0]["fix"])

    def test_a_clean_run_has_no_error_groups(self):
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1")])
        self.assertEqual(self.extract()["errors"], [])

    def test_summary_falls_back_to_the_command_then_the_file_name(self):
        self.main_thread(self.lines())
        calls = self.extract()["calls"]
        self.assertEqual(calls[1]["summary"], "grep nothing file")
        self.assertEqual(calls[3]["summary"], "Client.cs")

    def test_a_call_with_no_result_yet_has_zero_duration_and_no_output(self):
        self.main_thread(self.lines())
        c = self.extract()["calls"][3]
        self.assertEqual((c["ms"], c["output"], c["error"]), (0, "", False))

    def test_a_background_call_gets_its_real_finish_time_from_the_task_notification(self):
        note = ("<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>t1</tool-use-id>\n"
                "<status>completed</status>\n</task-notification>")
        self.main_thread([
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1",
                      tools=[("t1", "Bash", {"command": "codex exec", "run_in_background": True})]),
            tool_result(at(1, 2), SID, "t1", text="started"),
            line(type="queue-operation", operation="enqueue", timestamp=at(3, 2), sessionId=SID, content=note),
        ])
        c = self.extract()["calls"][0]
        self.assertEqual((c["background"], c["ms"], c["bgMs"]), (True, 2000, 122000))


class TestPrompts(RunFixture):
    def test_only_what_the_operator_typed_on_the_main_thread_is_kept(self):
        self.main_thread([
            prompt(at(1), SID, "Fix 1 and 2; the monitor reads the board directly"),
            prompt(at(2), SID, "<task-notification>done</task-notification>"),
            prompt(at(3), SID, "Another Claude session sent a message: hi"),
            assistant(at(4), SID, OPUS, usage(out=10), uuid="a1"),
        ])
        self.subagent("abc", [prompt(at(2), SID, "You are a dev agent."),
                              assistant(at(5), SID, OPUS, usage(out=10), uuid="b1")])
        self.assertEqual(self.extract()["prompts"],
                         [{"t": at(1), "text": "Fix 1 and 2; the monitor reads the board directly"}])


class TestTextHandling(RunFixture):
    def secret_run(self, **kw):
        self.main_thread([
            prompt(at(0, 30), SID, "use PESTPAC_PASSWORD=hunter2hunter2 please"),
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1", texts=["Reading the env file."],
                      tools=[("t1", "Bash", {"command": "curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwx' x"})]),
            tool_result(at(1, 1), SID, "t1", text="API_KEY=sk_live_0123456789\n" + "x" * 5000),
        ])
        return self.extract(**kw)

    def test_values_that_look_like_credentials_are_redacted(self):
        doc = self.secret_run()
        blob = json.dumps(doc)
        for leaked in ("hunter2hunter2", "abcdefghijklmnopqrstuvwx", "sk_live_0123456789"):
            self.assertNotIn(leaked, blob)
        self.assertIn("PESTPAC_PASSWORD=[redacted]", doc["prompts"][0]["text"])
        self.assertTrue(doc["calls"][0]["output"].startswith("API_KEY=[redacted]"))

    def test_long_text_is_clipped_and_flagged(self):
        c = self.secret_run(clip=100)["calls"][0]
        self.assertEqual((len(c["output"]), c["outputCut"], c["inputCut"]), (100, True, False))

    def test_text_false_keeps_timing_and_cost_but_no_text(self):
        doc = self.secret_run(text=False)
        c = doc["calls"][0]
        self.assertEqual((c["input"], c["output"], doc["turns"][0]["text"], doc["prompts"][0]["text"]), ("", "", "", ""))
        self.assertEqual((c["ms"], c["tool"]), (1000, "Bash"))
        self.assertGreater(doc["turns"][0]["cost"], 0)


class TestCodexLane(RunFixture):
    CWD = "C:\\Repos\\shop\\.worktrees\\S5"

    def rollout(self, name="rollout-a.jsonl", cwd=CWD, started=None, sid="cx-1", model="gpt-9-unlisted"):
        started = started or at(10)
        codex_file(os.path.join(self.codex_root, "2026", "11", "24"), name, [
            codex_session_meta(started, sid, cwd=cwd, originator="codex_exec"),
            codex_turn_context(at(10, 1), model, reasoning_effort="high"),
            codex_message(at(10, 2), "user", "Review this diff."),
            codex_custom_tool_call(at(10, 3), "c1", "exec", "git diff --stat"),
            codex_custom_tool_call_output(at(10, 5), "c1", [{"type": "input_text", "text": "3 files changed"}]),
            codex_token_count(at(10, 6), last=tok(inp=5000, cached=4000, out=200)),
            codex_function_call(at(10, 7), "c2", "shell", json.dumps({"command": "ls"})),
            codex_function_call_output(at(10, 8), "c2", json.dumps({"output": "boom", "metadata": {"exit_code": 2}})),
            codex_token_count(at(10, 9), last=tok(inp=6000, cached=5000, out=100)),
        ])

    def with_main(self):
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1")])

    def test_a_codex_session_started_under_cwd_in_the_window_joins_the_run(self):
        self.with_main()
        self.rollout()
        doc = self.extract(cwd="c:/repos/shop")   # parent directory, other slashes, other case
        lane = doc["lanes"][1]
        self.assertEqual((lane["id"], lane["name"], lane["kind"], lane["provider"]), ("codex:cx-1", "Codex", "external", "codex"))
        self.assertEqual((lane["turns"], lane["calls"], lane["errors"], lane["efforts"]), (2, 2, 1, ["high"]))
        self.assertEqual(lane["tok"], {"in": 2000, "out": 300, "read": 9000, "write": 0})

    def test_freeform_and_function_tool_calls_both_come_through(self):
        self.with_main()
        self.rollout()
        calls = [c for c in self.extract(cwd=self.CWD)["calls"] if c["lane"] == "codex:cx-1"]
        self.assertEqual([(c["tool"], c["input"], c["output"], c["ms"], c["error"]) for c in calls],
                         [("exec", "git diff --stat", "3 files changed", 2000, False),
                          ("shell", "ls", "boom", 1000, True)])

    def test_a_model_with_no_rate_card_is_counted_but_not_priced(self):
        self.with_main()
        self.rollout()
        lane = self.extract(cwd=self.CWD)["lanes"][1]
        self.assertEqual((lane["priced"], lane["cost"]), (False, 0.0))

    def test_codex_prompts_are_not_reported_as_operator_messages(self):
        self.with_main()
        self.rollout()
        self.assertEqual(self.extract(cwd=self.CWD)["prompts"], [])

    def test_sessions_elsewhere_or_outside_the_window_are_left_out(self):
        self.with_main()
        self.rollout(name="other-dir.jsonl", cwd="C:\\Repos\\shopping", sid="cx-2")   # a sibling, not a child
        self.rollout(name="too-late.jsonl", started="2026-11-24T12:00:00.000Z", sid="cx-3")
        self.assertEqual([l["id"] for l in self.extract(cwd="C:\\Repos\\shop")["lanes"]], ["main"])

    def test_no_cwd_means_no_codex_lanes(self):
        self.with_main()
        self.rollout()
        self.assertEqual([l["id"] for l in self.extract()["lanes"]], ["main"])


class TestProviderAdditions(RunFixture):
    def test_claude_turn_carries_effort_and_prompt_segment_carries_its_time(self):
        self.main_thread([prompt(at(1), SID, "hello"), assistant(at(2), SID, OPUS, usage(out=10), uuid="a1", effort="high")])
        recs = list(ClaudeProvider(root=self.claude_root).parse_file(os.path.join(self.claude_root, "projX", SID + ".jsonl")))
        self.assertEqual([r.effort for r in recs if isinstance(r, models.Turn)], ["high"])
        self.assertEqual([r.ts for r in recs if isinstance(r, models.Segment) and r.kind == "prompt"], [at(1)])

    def test_effort_is_none_when_the_log_does_not_state_it(self):
        self.main_thread([assistant(at(2), SID, OPUS, usage(out=10), uuid="a1")])
        self.assertEqual(self.extract()["lanes"][0]["efforts"], [])


class TestCli(RunFixture):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_run_help_exits_zero(self):
        code, out, _ = self.run_cli("run", "--help")
        self.assertEqual(code, 0)
        self.assertIn("--session", out)

    def test_unknown_session_exits_two_and_writes_nothing(self):
        saved = os.environ.get("CLAUDE_PROJECTS")
        os.environ["CLAUDE_PROJECTS"] = self.claude_root
        self.addCleanup(lambda: os.environ.pop("CLAUDE_PROJECTS", None) if saved is None
                        else os.environ.__setitem__("CLAUDE_PROJECTS", saved))
        target = os.path.join(self._tmp.name, "run.json")
        code, out, err = self.run_cli("run", "--session", "nope", "--start", START, "--end", END, "--out", target)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no transcript activity", err)
        self.assertFalse(os.path.exists(target))

    def test_out_writes_the_document_to_a_file(self):
        saved = os.environ.get("CLAUDE_PROJECTS")
        os.environ["CLAUDE_PROJECTS"] = self.claude_root
        self.addCleanup(lambda: os.environ.pop("CLAUDE_PROJECTS", None) if saved is None
                        else os.environ.__setitem__("CLAUDE_PROJECTS", saved))
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1")])
        target = os.path.join(self._tmp.name, "run.json")
        code, out, _ = self.run_cli("run", "--session", SID, "--start", START, "--end", END, "--out", target)
        self.assertEqual((code, out), (0, ""))
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["lanes"][0]["id"], "main")


if __name__ == "__main__":
    unittest.main()
