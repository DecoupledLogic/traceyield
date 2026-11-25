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
        providers = {"claude": ClaudeProvider(root=self.claude_root),
                     "codex": CodexProvider(root=self.codex_root, custom_tool_calls=True)}
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

    def test_records_with_no_request_id_merge_on_a_shared_message_id(self):
        u = usage(inp=1000, out=2000)
        self.main_thread([
            assistant(at(1), SID, OPUS, u, texts=["Looking."], uuid="a1", message_id="msg-1", effort="high"),
            assistant(at(1, 1), SID, OPUS, u, tools=[("t1", "Bash", {"command": "ls"})], uuid="a2", message_id="msg-1"),
            tool_result(at(1, 2), SID, "t1"),
        ])
        doc = self.extract()
        self.assertEqual(len(doc["turns"]), 1)
        self.assertAlmostEqual(doc["turns"][0]["cost"], 0.055)   # 1000*5 + 2000*25, once
        self.assertEqual(doc["turns"][0]["effort"], "high")      # stated on the first block only

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

    def split_request(self, first, second):
        u = usage(out=1000)
        self.main_thread([
            assistant(first, SID, OPUS, u, texts=["Planning."], uuid="a1", request_id="r1"),
            assistant(second, SID, OPUS, u, tools=[("t1", "Bash", {"command": "ls"})], uuid="a2", request_id="r1"),
            tool_result("2026-11-24T11:05:00.000Z", SID, "t1", text="done"),
            assistant(at(30), SID, OPUS, usage(out=10), uuid="b1", request_id="r2"),
        ])
        return self.extract()

    def test_a_request_that_began_before_the_window_takes_its_calls_with_it(self):
        doc = self.split_request("2026-11-24T09:59:59.500Z", "2026-11-24T10:00:00.500Z")
        self.assertEqual([t["id"] for t in doc["turns"]], ["main:2"])
        self.assertEqual(doc["calls"], [])            # no call left pointing at a turn that is not there

    def test_a_request_that_began_inside_keeps_blocks_and_results_logged_after_the_end(self):
        doc = self.split_request("2026-11-24T10:59:59.500Z", "2026-11-24T11:00:00.500Z")
        last = doc["turns"][-1]
        self.assertEqual((last["text"], len(last["calls"])), ("Planning.", 1))
        call = doc["calls"][0]
        self.assertEqual((call["turn"], call["output"], call["ms"]), (last["id"], "done", 299500))

    def test_every_call_points_at_a_turn_in_the_document(self):
        doc = self.split_request("2026-11-24T10:10:00.000Z", "2026-11-24T10:10:01.000Z")
        ids = {t["id"] for t in doc["turns"]}
        self.assertTrue(doc["calls"])
        self.assertTrue(all(c["turn"] in ids for c in doc["calls"]))

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
                         [{"t": at(1), "text": "Fix 1 and 2; the monitor reads the board directly", "textCut": False}])

    def test_a_window_holding_only_a_prompt_still_has_its_lane(self):
        self.main_thread([prompt(at(1), SID, "Are you there?"),
                          assistant("2026-11-24T12:00:00.000Z", SID, OPUS, usage(out=10), uuid="a1")])
        doc = self.extract()
        self.assertEqual([l["id"] for l in doc["lanes"]], ["main"])
        self.assertEqual((doc["lanes"][0]["turns"], doc["lanes"][0]["calls"], len(doc["prompts"])), (0, 0, 1))


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

    def test_structured_arguments_and_quoted_values_are_redacted(self):
        self.main_thread([
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1", request_id="r1",
                      tools=[("t1", "mcp__crm__connect", {"host": "x", "api_key": "example-marker-123",
                                                          "nested": {"clientSecret": "marker two words"}})]),
            tool_result(at(1, 1), SID, "t1", text='{"password": "marker with spaces", "ok": true}'),
            assistant(at(2), SID, OPUS, usage(out=10), uuid="a2", request_id="r2",
                      tools=[("t2", "Bash", {"command": "export TOKEN='marker in quotes'"})]),
        ])
        blob = json.dumps(self.extract())
        for leaked in ("example-marker-123", "marker two words", "marker with spaces", "marker in quotes"):
            self.assertNotIn(leaked, blob)
        self.assertIn("[redacted]", blob)
        self.assertIn('\\"host\\": \\"x\\"', blob)      # an ordinary field is left alone

    def test_no_text_leaves_nothing_from_the_transcript_anywhere(self):
        marker = "ZEBRA-MARKER"
        self.main_thread([
            prompt(at(0, 30), SID, f"please {marker}"),
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1", texts=[f"saying {marker}"],
                      tools=[("t1", "Bash", {"command": f"echo {marker}", "description": f"Echo {marker}"})]),
            tool_result(at(1, 1), SID, "t1", text=f"{marker}"),
        ])
        self.subagent("abc", [assistant(at(2), SID, OPUS, usage(out=10), uuid="b1")],
                      meta={"agentType": "dev-agent", "description": f"Build {marker}"})
        doc = self.extract(text=False)
        self.assertNotIn(marker, json.dumps(doc))
        self.assertEqual((doc["calls"][0]["summary"], doc["calls"][0]["tool"]), ("", "Bash"))
        self.assertIn(marker, json.dumps(self.extract()))      # and it is there with text on

    def test_replies_and_prompts_are_cut_with_a_flag_and_summaries_are_one_line(self):
        self.main_thread([
            prompt(at(0, 30), SID, "p" * 5000),
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1", texts=["r" * 5000],
                      tools=[("t1", "Bash", {"command": "ls", "description": "First line\nsecond line"})]),
        ])
        doc = self.extract(clip=100)
        turn, ask, call = doc["turns"][0], doc["prompts"][0], doc["calls"][0]
        self.assertEqual((len(turn["text"]), turn["textCut"]), (100, True))
        self.assertEqual((len(ask["text"]), ask["textCut"]), (100, True))
        self.assertEqual(call["summary"], "First line second line")
        wide = self.extract(clip=100000)
        self.assertEqual((len(wide["turns"][0]["text"]), wide["turns"][0]["textCut"]), (runs.REPLY_CLIP, True))

    def test_a_secret_straddling_the_cut_is_redacted_before_cutting(self):
        self.main_thread([
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1", tools=[("t1", "Bash", {"command": "env"})]),
            tool_result(at(1, 1), SID, "t1", text="x" * 90 + " API_KEY=abcdefghijklmnop"),
        ])
        self.assertNotIn("abcdef", self.extract(clip=105)["calls"][0]["output"])

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

    def test_the_working_directory_is_not_written_into_the_document(self):
        self.with_main()
        self.rollout()
        doc = self.extract(cwd=self.CWD)
        self.assertEqual(doc["source"], {"session": SID, "codex": True})
        self.assertNotIn("shop", json.dumps(doc))

    def test_calls_and_cost_belong_to_the_request_whose_usage_follows_them(self):
        self.with_main()
        self.rollout(model="gpt-5.5")      # priced: 5.00 in, 30.00 out, cached at 0.1x
        doc = self.extract(cwd=self.CWD)
        turns = [t for t in doc["turns"] if t["lane"] == "codex:cx-1"]
        calls = [c for c in doc["calls"] if c["lane"] == "codex:cx-1"]
        self.assertEqual([c["turn"] for c in calls], [turns[0]["id"], turns[1]["id"]])
        # turn 1: 1000 fresh*5 + 4000 cached*0.5 + 200 out*30 = 13000 / 1e6
        self.assertAlmostEqual(turns[0]["cost"], 0.013)
        self.assertAlmostEqual(calls[0]["cost"], 0.013)
        # turn 2: 1000*5 + 5000*0.5 + 100*30 = 10500 / 1e6
        self.assertAlmostEqual(calls[1]["cost"], 0.0105)
        self.assertEqual(doc["lanes"][1]["priced"], True)

    def test_the_scheduled_ingest_still_leaves_freeform_calls_as_raw_events(self):
        self.rollout()
        path = os.path.join(self.codex_root, "2026", "11", "24", "rollout-a.jsonl")
        recs = list(CodexProvider(root=self.codex_root).parse_file(path))     # the default, as ingest builds it
        self.assertEqual([r.call_id for r in recs if isinstance(r, models.ToolCall)], ["c2", "c2"])
        raw = [json.loads(r.raw)["payload"]["type"] for r in recs if isinstance(r, models.RawEvent)]
        self.assertEqual(raw, ["custom_tool_call", "custom_tool_call_output"])

    def test_path_matching_follows_the_rules_of_the_path_it_was_given(self):
        self.with_main()
        self.rollout(name="posix.jsonl", cwd="/home/u/proj/sub", sid="cx-p")
        ids = lambda cwd: [l["id"] for l in self.extract(cwd=cwd)["lanes"]]
        self.assertEqual(ids("/home/u/proj"), ["main", "codex:cx-p"])
        self.assertEqual(ids("/home/u/other/../proj/"), ["main", "codex:cx-p"])   # same directory, spelled differently
        self.assertEqual(ids("/home/u/Proj"), ["main"])                           # POSIX paths are case-sensitive
        self.assertEqual(ids("/home/u/pro"), ["main"])                            # a prefix is not a parent

    def test_a_rollout_filed_outside_a_dated_folder_is_still_found(self):
        self.with_main()
        codex_file(self.codex_root, "loose.jsonl", [
            codex_session_meta(at(10), "cx-loose", cwd=self.CWD),
            codex_token_count(at(10, 6), last=tok(inp=10, out=1)),
        ])
        self.assertEqual([l["id"] for l in self.extract(cwd=self.CWD)["lanes"]], ["main", "codex:cx-loose"])

    def test_a_dated_folder_far_from_the_window_is_not_opened(self):
        self.with_main()
        codex_file(os.path.join(self.codex_root, "2026", "09", "01"), "misfiled.jsonl", [
            codex_session_meta(at(10), "cx-old", cwd=self.CWD),
            codex_token_count(at(10, 6), last=tok(inp=10, out=1)),
        ])
        self.assertEqual([l["id"] for l in self.extract(cwd=self.CWD)["lanes"]], ["main"])

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


class TestRealLogShapes(RunFixture):
    """Shapes found in real logs by the pre-PR review, rebuilt as synthetic fixtures."""

    def test_a_failed_codex_exec_is_an_error(self):
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1")])
        codex_file(os.path.join(self.codex_root, "2026", "11", "24"), "r.jsonl", [
            codex_session_meta(at(10), "cx-f", cwd="/w/proj"),
            codex_turn_context(at(10, 1), "gpt-5.5"),
            codex_custom_tool_call(at(10, 3), "c1", "exec", "run()"),
            codex_custom_tool_call_output(at(10, 5), "c1", [{"type": "input_text", "text": "Script failed\nWall time 0.1 seconds\nOutput:\nboom"}]),
            codex_custom_tool_call(at(10, 6), "c2", "exec", "run()"),
            codex_custom_tool_call_output(at(10, 7), "c2", "Script completed\nWall time 0.1 seconds\nOutput:\n"),
            codex_token_count(at(10, 8), last=tok(inp=10, out=1)),
        ])
        doc = self.extract(cwd="/w/proj")
        self.assertEqual([c["error"] for c in doc["calls"]], [True, False])
        self.assertEqual(doc["lanes"][1]["errors"], 1)
        self.assertEqual([e["calls"] for e in doc["errors"]], [[1]])

    def test_background_finish_is_the_enqueue_notice_not_the_later_remove(self):
        note = "<task-notification><tool-use-id>t1</tool-use-id><status>completed</status></task-notification>"
        self.main_thread([
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1",
                      tools=[("t1", "Bash", {"command": "long", "run_in_background": True})]),
            tool_result(at(1, 2), SID, "t1", text="started"),
            line(type="queue-operation", operation="enqueue", timestamp=at(3), sessionId=SID, content=note),
            line(type="queue-operation", operation="remove", timestamp=at(20), sessionId=SID, content=note),
        ])
        doc = self.extract()
        self.assertEqual(doc["calls"][0]["bgMs"], 120000)
        self.assertEqual(doc["lanes"][0]["end"], at(3))

    def test_a_synthetic_placeholder_record_is_not_a_turn(self):
        self.main_thread([
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1"),
            assistant(at(2), SID, "<synthetic>", usage(), uuid="a2", texts=["API Error"]),
        ])
        lane = self.extract()["lanes"][0]
        self.assertEqual((lane["turns"], lane["models"], lane["priced"]), (1, [OPUS], True))

    def test_a_window_with_no_offset_is_read_as_utc(self):
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1")])
        doc = self.extract(start="2026-11-24T10:00:00", end="2026-11-24T11:00:00")
        self.assertEqual(len(doc["turns"]), 1)
        self.assertEqual(doc["window"], {"start": "2026-11-24T10:00:00Z", "end": "2026-11-24T11:00:00Z"})
        self.assertEqual(len(self.extract(start="2026-11-24", end="2026-11-25")["turns"]), 1)
        self.assertEqual(runs.utc("2026-11-24T05:00:00-05:00"), "2026-11-24T10:00:00Z")
        with self.assertRaises(ValueError):
            self.extract(start="yesterday")

    def test_a_session_written_under_two_project_folders_keeps_both(self):
        write_transcript(self.claude_root, "projA", SID + ".jsonl", [assistant(at(1), SID, OPUS, usage(out=10), uuid="a1")])
        write_transcript(self.claude_root, "projB", SID + ".jsonl", [assistant(at(2), SID, OPUS, usage(out=10), uuid="b1"),
                                                                 assistant(at(3), SID, OPUS, usage(out=10), uuid="b2")])
        write_transcript(self.claude_root, os.path.join("projB", SID, "subagents"), "agent-x.jsonl",
                         [assistant(at(4), SID, OPUS, usage(out=10), uuid="c1")])
        self.assertEqual([(l["id"], l["name"], l["turns"]) for l in self.extract()["lanes"]],
                         [("main", "Main thread", 1), ("main-2", "Main thread 2", 2), ("x", "x", 1)])

    def test_a_window_at_the_edge_of_the_calendar_does_not_crash_codex_discovery(self):
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1")])
        codex_file(os.path.join(self.codex_root, "2026", "11", "24"), "r.jsonl", [
            codex_session_meta(at(10), "cx-w", cwd="/w/proj"), codex_token_count(at(10, 8), last=tok(inp=10, out=1))])
        for start, end in (("1970-01-01T00:00:00Z", "9999-12-31T23:59:59Z"), ("1900-01-01T00:00:00Z", "2030-01-01T00:00:00Z")):
            self.assertEqual([l["id"] for l in self.extract(start=start, end=end, cwd="/w/proj")["lanes"]],
                             ["main", "codex:cx-w"])

    def test_slash_commands_are_kept_and_harness_lines_are_dropped(self):
        self.main_thread([
            prompt(at(1), SID, "<command-message>workflow-deliver</command-message>\n"
                               "<command-name>/workflow-deliver</command-name>\n<command-args>E4-F1-S1</command-args>"),
            prompt(at(2), SID, "<bash-input>git status</bash-input>"),
            prompt(at(3), SID, "<pasted_content id=\"x\">a log</pasted_content> look at this"),
            line(timestamp=at(4), sessionId=SID, isMeta=True, message={"role": "user", "content": "(Re-invocation of /workflow-plan)"}),
            prompt(at(5), SID, "<local-command-stdout>ok</local-command-stdout>"),
            prompt(at(6), SID, "<system-reminder>be good</system-reminder>"),
            assistant(at(7), SID, OPUS, usage(out=10), uuid="a1"),
        ])
        self.assertEqual([p["text"] for p in self.extract()["prompts"]],
                         ["/workflow-deliver E4-F1-S1", "! git status", "<pasted_content id=\"x\">a log</pasted_content> look at this"])

    def test_redaction_is_linear_on_long_joined_tokens(self):
        import time
        started = time.perf_counter()
        for unit in ("abcdefgh-", "ab.cd", "aB3_x-9Zq"):
            self.assertEqual(len(runs.scrub(unit * 20000)), len(unit) * 20000)
        self.assertLess(time.perf_counter() - started, 2.0)

    def test_a_lone_surrogate_does_not_break_the_written_file(self):
        saved = os.environ.get("CLAUDE_PROJECTS")
        os.environ["CLAUDE_PROJECTS"] = self.claude_root
        self.addCleanup(lambda: os.environ.pop("CLAUDE_PROJECTS", None) if saved is None
                        else os.environ.__setitem__("CLAUDE_PROJECTS", saved))
        self.main_thread([
            assistant(at(1), SID, OPUS, usage(out=10), uuid="a1", tools=[("t1", "Bash", {"command": "echo"})]),
            tool_result(at(1, 1), SID, "t1", text="half an emoji \ud83d here"),
        ])
        target = os.path.join(self._tmp.name, "run.json")
        self.assertEqual(cli.main(["run", "--session", SID, "--start", START, "--end", END, "--out", target]), 0)
        with open(target, encoding="utf-8") as fh:
            self.assertIn("half an emoji", json.load(fh)["calls"][0]["output"])

    def test_common_credential_shapes_are_redacted_and_token_counts_are_not(self):
        secret = "hunter2hunter2"
        for text in ('curl -d "{\\"password\\":\\"' + secret + '\\"}" https://x',
                     "mysql --password " + secret,
                     "tool --api-key=" + secret,
                     "psql postgres://admin:" + secret + "@db/prod",
                     "DefaultEndpointsProtocol=https;AccountKey=" + secret + ";",
                     "Server=x;Pwd=" + secret + ";",
                     "git push https://ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2@github.com/o/r"):
            out = runs.scrub(text)
            self.assertNotIn(secret, out, text)
            self.assertNotIn("a1B2c3D4e5F6g7H8i9J0k1L2", out, text)
            self.assertIn("[redacted]", out, text)
        for text in ("max_tokens: 100000000", "input_tokens=123456789", '"output_tokens": 4862', "--max-tokens 128000"):
            self.assertEqual(runs.scrub(text), text)
        self.assertEqual(runs._redact({"max_tokens": 128000, "token": "abc"}), {"max_tokens": 128000, "token": "[redacted]"})


class TestStoreUntouched(RunFixture):
    def test_runs_module_imports_neither_the_store_nor_the_report(self):
        import ast
        tree = ast.parse(open(runs.__file__, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[-1] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[-1])
                imported |= {a.name for a in node.names}
        self.assertEqual(imported & {"canonical", "report", "sqlite3", "persistence", "aggregation"}, set())

    def test_extracting_a_run_creates_no_machine_directory_or_database(self):
        from traceyield import paths
        saved = os.environ.get("TRACEYIELD_MACHINE")
        os.environ["TRACEYIELD_MACHINE"] = "run-extract-test-never-created"
        self.addCleanup(lambda: os.environ.pop("TRACEYIELD_MACHINE", None) if saved is None
                        else os.environ.__setitem__("TRACEYIELD_MACHINE", saved))
        self.main_thread([assistant(at(1), SID, OPUS, usage(out=10), uuid="a1",
                                    tools=[("t1", "Bash", {"command": "ls"})]),
                          tool_result(at(1, 1), SID, "t1")])
        self.assertEqual(len(self.extract()["calls"]), 1)
        self.assertFalse(os.path.exists(paths.machine_dir()))
        self.assertFalse(os.path.exists(paths.db_file()))


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

    def test_a_window_that_is_not_a_timestamp_exits_two(self):
        code, _, err = self.run_cli("run", "--session", SID, "--start", "yesterday", "--end", END)
        self.assertEqual(code, 2)
        self.assertIn("ISO 8601", err)

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
