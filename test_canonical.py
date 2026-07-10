#!/usr/bin/env python3
"""
Tests for canonical.py — the provider-neutral SQLite usage store.

Stdlib-only (unittest; also runs under pytest):
    python -m unittest test_canonical

Fixtures are hand-built transcript lines run through ClaudeProvider + ingest()
into an in-memory db, then asserted with SQL. The seams that keep tests off the
real ~/.claude: ClaudeProvider(root=tmp) and open_db(":memory:"). Where costs
would be involved they aren't — the canonical store deliberately holds tokens,
not dollars (cost stays a query-time projection).
"""
import json, os, tempfile, unittest, warnings
import report, canonical

warnings.simplefilter("ignore", ResourceWarning)


# --------------------------------------------------------------- fixture helpers
def line(**kw):
    return json.dumps(kw)

def assistant(ts, sid, model, usage, tools=(), texts=(), thinking=None, uuid=None, parent=None):
    content = []
    for tid, name, inp in tools:
        content.append({"type": "tool_use", "id": tid, "name": name, "input": inp})
    for txt in texts:
        content.append({"type": "text", "text": txt})
    if thinking is not None:
        content.append({"type": "thinking", "thinking": thinking, "signature": "SIG"})
    o = {"timestamp": ts, "sessionId": sid,
         "message": {"role": "assistant", "model": model, "usage": usage, "content": content}}
    if uuid: o["uuid"] = uuid
    if parent: o["parentUuid"] = parent
    return line(**o)

def tool_result(ts, sid, tool_use_id, is_error=False, text="ok"):
    return line(timestamp=ts, sessionId=sid,
                message={"role": "user", "content": [{"type": "tool_result",
                         "tool_use_id": tool_use_id, "is_error": is_error, "content": text}]})

def prompt(ts, sid, text, uuid=None):
    o = {"timestamp": ts, "sessionId": sid, "message": {"role": "user", "content": text}}
    if uuid: o["uuid"] = uuid
    return line(**o)

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

def ingest_lines(lines, project="projX", name="conv.jsonl", capture="structural"):
    """Write a transcript, ingest it, return (conn, tmpdir-keepalive)."""
    tmp = tempfile.TemporaryDirectory()
    write_transcript(tmp.name, project, name, lines)
    conn = canonical.open_db(":memory:")
    canonical.ingest(conn, [canonical.ClaudeProvider(root=tmp.name)], capture=capture)
    return conn, tmp


# --------------------------------------------------------------- pure helpers
class TestHelpers(unittest.TestCase):
    def test_tool_kind_normalization(self):
        self.assertEqual(canonical.tool_kind("Edit"), "file_edit")
        self.assertEqual(canonical.tool_kind("Write"), "file_edit")
        self.assertEqual(canonical.tool_kind("apply_patch"), "file_edit")   # codex maps to same kind
        self.assertEqual(canonical.tool_kind("Bash"), "shell")
        self.assertEqual(canonical.tool_kind("shell"), "shell")
        self.assertEqual(canonical.tool_kind("Read"), "file_read")
        self.assertEqual(canonical.tool_kind("mcp__server__do"), "mcp")
        self.assertEqual(canonical.tool_kind("Frobnicate"), "other")
        self.assertIsNone(canonical.tool_kind(None))

    def test_sha_deterministic_and_none_safe(self):
        self.assertEqual(canonical.sha("abc"), canonical.sha("abc"))
        self.assertNotEqual(canonical.sha("abc"), canonical.sha("abd"))
        self.assertIsNone(canonical.sha(None))

    def test_ms_delta(self):
        a = canonical._ms("2026-01-01T00:00:00Z")
        b = canonical._ms("2026-01-01T00:00:01Z")
        self.assertEqual(b - a, 1000)
        self.assertIsNone(canonical._ms("garbage"))

    def test_open_db_sets_schema_version(self):
        conn = canonical.open_db(":memory:")
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], canonical.SCHEMA_VERSION)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"session", "turn", "tool_call", "segment", "raw_event"} <= tables)


# --------------------------------------------------------------- turns & tokens
class TestTurns(unittest.TestCase):
    def setUp(self):
        self.conn, self.tmp = ingest_lines([
            assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4-8",
                      usage(inp=100, out=50, cr=1000, w5m=200, w1h=300),
                      tools=[("t1", "Read", {"file_path": "/x"})], uuid="u1"),
            tool_result("2026-01-01T10:00:01Z", "s1", "t1"),
            assistant("2026-01-01T10:00:05Z", "s1", "claude-opus-4-8",
                      usage(inp=10, out=10), uuid="u2", parent="u1"),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_turn_rows_and_token_vector(self):
        r = self.conn.execute("SELECT input_fresh,cache_read,cache_write_5m,cache_write_1h,output,tier,model "
                              "FROM turn WHERE turn_id='u1'").fetchone()
        self.assertEqual(r, (100, 1000, 200, 300, 50, "opus", "claude-opus-4-8"))

    def test_turn_count(self):
        self.assertEqual(self.conn.execute("SELECT count(*) FROM turn").fetchone()[0], 2)

    def test_wall_ms_from_consecutive_turns(self):
        # second turn is 5s after the first in the same session
        w = self.conn.execute("SELECT wall_ms FROM turn WHERE turn_id='u2'").fetchone()[0]
        self.assertEqual(w, 5000)
        self.assertIsNone(self.conn.execute("SELECT wall_ms FROM turn WHERE turn_id='u1'").fetchone()[0])

    def test_parent_and_ntools_recorded(self):
        r = self.conn.execute("SELECT parent_turn_id,n_tool_calls FROM turn WHERE turn_id='u1'").fetchone()
        self.assertEqual(r, (None, 1))   # u1 is the root turn; it issued 1 tool
        r2 = self.conn.execute("SELECT parent_turn_id,n_tool_calls FROM turn WHERE turn_id='u2'").fetchone()
        self.assertEqual(r2, ("u1", 0))  # u2's parent is u1; it issued none

    def test_cache_creation_fallback_to_5m(self):
        conn, tmp = ingest_lines([
            assistant("2026-01-02T00:00:00Z", "s2", "claude-haiku-4-5",
                      usage(inp=1000, out=500, cc=400), uuid="h1")])
        r = conn.execute("SELECT cache_write_5m,cache_write_1h FROM turn WHERE turn_id='h1'").fetchone()
        self.assertEqual(r, (400, 0))
        tmp.cleanup()

    def test_unknown_model_still_recorded_with_null_tier(self):
        # analyze() SKIPS unknown models; the canonical store keeps them (raw model
        # never lost) with tier NULL — cost isn't stored so mispricing is impossible.
        conn, tmp = ingest_lines([
            assistant("2026-01-03T00:00:00Z", "s3", "gpt-9-ultra", usage(inp=10, out=5), uuid="g1")])
        r = conn.execute("SELECT model,tier FROM turn WHERE turn_id='g1'").fetchone()
        self.assertEqual(r, ("gpt-9-ultra", None))
        tmp.cleanup()

    def test_no_cost_column_exists(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(turn)")}
        self.assertNotIn("cost", cols)   # cost is a query-time projection, never stored


# --------------------------------------------------------------- tool calls
class TestToolCalls(unittest.TestCase):
    def setUp(self):
        self.conn, self.tmp = ingest_lines([
            assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4-8", usage(inp=10, out=5),
                      tools=[("t1", "Bash", {"command": "ls"})], uuid="u1"),
            tool_result("2026-01-01T10:00:02Z", "s1", "t1", is_error=True, text="command not found"),
            assistant("2026-01-01T10:00:05Z", "s1", "claude-opus-4-8", usage(inp=10, out=5),
                      tools=[("t2", "Read", {"file_path": "/y"})], uuid="u2"),
            tool_result("2026-01-01T10:00:06Z", "s1", "t2", is_error=False, text="ok"),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_call_name_kind_and_turn_join(self):
        r = self.conn.execute("SELECT name,kind,turn_id FROM tool_call WHERE call_id='t1'").fetchone()
        self.assertEqual(r, ("Bash", "shell", "u1"))

    def test_result_sets_ok_and_error_class(self):
        r = self.conn.execute("SELECT ok,error_class,latency_ms FROM tool_call WHERE call_id='t1'").fetchone()
        self.assertEqual(r[0], 0)                       # failed
        self.assertEqual(r[1], "shell_cmd_not_found")   # unified taxonomy via report.classify
        self.assertEqual(r[2], 2000)                    # 10:00:02 - 10:00:00

    def test_ok_result(self):
        r = self.conn.execute("SELECT ok,error_class FROM tool_call WHERE call_id='t2'").fetchone()
        self.assertEqual(r, (1, None))

    def test_call_and_result_are_one_row(self):
        # the tool_use insert + the tool_result update collapse onto call_id
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tool_call").fetchone()[0], 2)


# --------------------------------------------------------------- segments & capture modes
class TestSegments(unittest.TestCase):
    LINES = [
        prompt("2026-01-01T09:59:00Z", "s1", "please list files", uuid="p1"),
        assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4-8", usage(inp=10, out=5),
                  tools=[("t1", "Bash", {"command": "ls"})],
                  texts=["Here you go."], thinking="", uuid="u1"),
        tool_result("2026-01-01T10:00:01Z", "s1", "t1", text="file listing output"),
    ]

    def test_structural_stores_hash_not_text(self):
        conn, tmp = ingest_lines(self.LINES, capture="structural")
        rows = dict((k, (ln, sh, tx)) for k, ln, sh, tx in conn.execute(
            "SELECT kind,length,sha256,text FROM segment"))
        # response text present in fixture → length + hash recorded, text withheld
        ln, sh, tx = rows["response"]
        self.assertEqual(ln, len("Here you go."))
        self.assertEqual(sh, canonical.sha("Here you go."))
        self.assertIsNone(tx)
        # tool args + output also structural-only
        self.assertIsNone(rows["tool_args"][2])
        self.assertIsNone(rows["tool_output"][2])
        self.assertIsNone(rows["prompt"][2])
        tmp.cleanup()

    def test_verbatim_stores_text(self):
        conn, tmp = ingest_lines(self.LINES, capture="verbatim")
        got = dict(conn.execute("SELECT kind,text FROM segment"))
        self.assertEqual(got["response"], "Here you go.")
        self.assertEqual(got["prompt"], "please list files")
        self.assertEqual(got["tool_output"], "file listing output")
        self.assertEqual(json.loads(got["tool_args"]), {"command": "ls"})
        tmp.cleanup()

    def test_redacted_reasoning_is_count_only_with_signature_provenance(self):
        # thinking="" (redacted) → text unavailable even in verbatim, but the
        # signature is hashed so the reasoning is still provenance-tracked (§2.4).
        conn, tmp = ingest_lines(self.LINES, capture="verbatim")
        r = conn.execute("SELECT length,text,text_available,sha256 FROM segment WHERE kind='reasoning'").fetchone()
        self.assertEqual(r[0], 0)                 # length
        self.assertIsNone(r[1])                   # no words
        self.assertEqual(r[2], 0)                 # text_available=False
        self.assertEqual(r[3], canonical.sha("SIG"))
        tmp.cleanup()

    def test_reasoning_summary_treated_as_text(self):
        # When a summary IS present (the Codex-style case), it becomes the text.
        conn, tmp = ingest_lines([
            assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4-8", usage(inp=10, out=5),
                      thinking="short reasoning summary", uuid="u1")], capture="verbatim")
        r = conn.execute("SELECT text,text_available FROM segment WHERE kind='reasoning'").fetchone()
        self.assertEqual(r, ("short reasoning summary", 1))
        tmp.cleanup()


# --------------------------------------------------------------- sessions
class TestSessions(unittest.TestCase):
    def test_session_metadata_and_span(self):
        conn, tmp = ingest_lines([
            line(timestamp="2026-01-01T10:00:00Z", sessionId="s1", cwd="/home/u/proj",
                 gitBranch="main", version="1.2.3",
                 message={"role": "assistant", "model": "claude-opus-4-8",
                          "usage": usage(inp=10, out=5), "content": []}),
            assistant("2026-01-02T10:00:00Z", "s1", "claude-opus-4-8", usage(inp=1, out=1), uuid="u2"),
        ])
        r = conn.execute("SELECT provider,cwd,git_branch,cli_version,first_ts,last_ts,machine_id "
                         "FROM session WHERE session_id='s1'").fetchone()
        self.assertEqual(r[0], "claude")
        self.assertEqual(r[1], "/home/u/proj")
        self.assertEqual(r[2], "main")
        self.assertEqual(r[3], "1.2.3")
        self.assertEqual(r[4], "2026-01-01T10:00:00Z")
        self.assertEqual(r[5], "2026-01-02T10:00:00Z")
        self.assertEqual(r[6], report.machine_id())
        tmp.cleanup()

    def test_project_label_from_folder(self):
        conn, tmp = ingest_lines([
            assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4-8", usage(inp=1, out=1), uuid="u1")],
            project="myrepo")
        self.assertEqual(conn.execute("SELECT project FROM session").fetchone()[0], "myrepo")
        tmp.cleanup()


# --------------------------------------------------------------- raw_event escape hatch
class TestRawEvent(unittest.TestCase):
    def test_unmodeled_line_captured(self):
        conn, tmp = ingest_lines([
            line(timestamp="2026-01-01T10:00:00Z", sessionId="s1", type="system",
                 subtype="hook", content="some system notice"),
            assistant("2026-01-01T10:00:01Z", "s1", "claude-opus-4-8", usage(inp=1, out=1), uuid="u1"),
        ], capture="structural")
        r = conn.execute("SELECT type,sha256,raw FROM raw_event").fetchone()
        self.assertEqual(r[0], "system")
        self.assertIsNotNone(r[1])       # hash always kept
        self.assertIsNone(r[2])          # raw withheld in structural mode
        tmp.cleanup()

    def test_raw_capped_in_verbatim(self):
        big = "x" * (canonical.RAW_CAP * 2)
        conn, tmp = ingest_lines([
            line(timestamp="2026-01-01T10:00:00Z", sessionId="s1", type="world_state", blob=big),
            assistant("2026-01-01T10:00:01Z", "s1", "claude-opus-4-8", usage(inp=1, out=1), uuid="u1"),
        ], capture="verbatim")
        raw = conn.execute("SELECT raw FROM raw_event WHERE type='world_state'").fetchone()[0]
        self.assertIsNotNone(raw)
        self.assertLessEqual(len(raw), canonical.RAW_CAP)   # clamped, not unbounded
        tmp.cleanup()


# --------------------------------------------------------------- idempotency & resilience
class TestIngestSemantics(unittest.TestCase):
    def test_reingest_is_idempotent(self):
        lines = [
            prompt("2026-01-01T09:59:00Z", "s1", "hi", uuid="p1"),
            assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4-8", usage(inp=10, out=5),
                      tools=[("t1", "Bash", {"command": "ls"})], texts=["done"], uuid="u1"),
            tool_result("2026-01-01T10:00:01Z", "s1", "t1", text="out"),
        ]
        tmp = tempfile.TemporaryDirectory()
        write_transcript(tmp.name, "p", "c.jsonl", lines)
        conn = canonical.open_db(":memory:")
        prov = [canonical.ClaudeProvider(root=tmp.name)]
        counts = lambda: tuple(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                               for t in ("session", "turn", "tool_call", "segment", "raw_event"))
        canonical.ingest(conn, prov)
        first = counts()
        canonical.ingest(conn, prov)             # run again — must not grow
        self.assertEqual(counts(), first)
        tmp.cleanup()

    def test_result_before_call_still_links(self):
        # defensive: a tool_result whose tool_use we somehow ingest later still
        # collapses onto one row (upsert on call_id), never a phantom duplicate.
        conn, tmp = ingest_lines([
            tool_result("2026-01-01T10:00:01Z", "s1", "t1", is_error=True, text="no such file"),
            assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4-8", usage(inp=1, out=1),
                      tools=[("t1", "Read", {"file_path": "/z"})], uuid="u1"),
        ])
        rows = conn.execute("SELECT name,ok,error_class FROM tool_call WHERE call_id='t1'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], ("Read", 0, "file_not_found"))
        tmp.cleanup()

    def test_malformed_line_does_not_abort_file(self):
        conn, tmp = ingest_lines([
            "{ not valid json",
            assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4-8", usage(inp=1, out=1), uuid="u1"),
            "another bad line }",
        ])
        self.assertEqual(conn.execute("SELECT count(*) FROM turn").fetchone()[0], 1)
        tmp.cleanup()

    def test_ingest_reports_counts(self):
        tmp = tempfile.TemporaryDirectory()
        write_transcript(tmp.name, "p", "c.jsonl", [
            assistant("2026-01-01T10:00:00Z", "s1", "claude-opus-4-8", usage(inp=1, out=1), uuid="u1")])
        conn = canonical.open_db(":memory:")
        files, recs = canonical.ingest(conn, [canonical.ClaudeProvider(root=tmp.name)])
        self.assertEqual(files, 1)
        self.assertGreaterEqual(recs, 2)   # at least a Turn + a Session
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
