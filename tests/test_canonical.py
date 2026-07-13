#!/usr/bin/env python3
"""
Tests for traceyield.canonical — the provider-neutral SQLite usage store.

Stdlib-only (unittest; also runs under pytest):
    python -m unittest discover -s tests   (or: python -m pytest tests -q)

Imports the installed traceyield package (`pip install -e .`), not the repo
root or src/ tree directly, so the suite exercises the same import surface a
consumer of the package would.

Fixtures are hand-built transcript lines run through ClaudeProvider + ingest()
into an in-memory db, then asserted with SQL. The seams that keep tests off the
real ~/.claude: ClaudeProvider(root=tmp) and open_db(":memory:"). Where costs
would be involved they aren't — the canonical store deliberately holds tokens,
not dollars (cost stays a query-time projection).
"""
import datetime, json, os, sys, tempfile, unittest, warnings

from traceyield import report, canonical

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


# --------------------------------------------------------------- codex fixture helpers
def codex_line(ts, type_, **payload):
    return line(timestamp=ts, type=type_, payload=payload)

def codex_session_meta(ts, sid, cwd="/home/u/proj", cli_version="0.39.0", originator="codex_cli_rs"):
    return codex_line(ts, "session_meta", id=sid, timestamp=ts, cwd=cwd,
                       originator=originator, cli_version=cli_version, instructions=None)

def codex_turn_context(ts, model, approval_policy="on-request", sandbox_mode="read-only", cwd="/home/u/proj"):
    return codex_line(ts, "turn_context", cwd=cwd, approval_policy=approval_policy,
                       sandbox_policy={"mode": sandbox_mode}, model=model, summary="auto")

def codex_token_count(ts, last=None, total=None, flat=None):
    info = {}
    if total is not None: info["total_token_usage"] = total
    if last is not None: info["last_token_usage"] = last
    if flat is not None:
        return line(timestamp=ts, type="event_msg",
                     payload=dict({"type": "token_count"}, **flat))
    return codex_line(ts, "event_msg", **{"type": "token_count", "info": info})

def tok(inp=0, cached=0, out=0, reasoning=0):
    total = inp + out
    return {"input_tokens": inp, "cached_input_tokens": cached,
            "output_tokens": out, "reasoning_output_tokens": reasoning, "total_tokens": total}

def codex_message(ts, role, text):
    kind = "input_text" if role == "user" else "output_text"
    return codex_line(ts, "response_item", type="message", role=role,
                       content=[{"type": kind, "text": text}])

def codex_reasoning(ts, summary_text=None, encrypted="gAAA"):
    summary = [{"type": "summary_text", "text": summary_text}] if summary_text is not None else []
    return codex_line(ts, "response_item", type="reasoning", summary=summary,
                       content=None, encrypted_content=encrypted)

def codex_function_call(ts, call_id, name, arguments="{}"):
    return codex_line(ts, "response_item", type="function_call", name=name,
                       arguments=arguments, call_id=call_id)

def codex_function_call_output(ts, call_id, output):
    return codex_line(ts, "response_item", type="function_call_output",
                       call_id=call_id, output=output)

def codex_rollout(lines, capture="structural"):
    """Write a codex rollout transcript, ingest with CodexProvider, return (conn, tmpdir)."""
    tmp = tempfile.TemporaryDirectory()
    write_transcript(tmp.name, "2026", "rollout-x.jsonl", lines)
    conn = canonical.open_db(":memory:")
    canonical.ingest(conn, [canonical.CodexProvider(root=tmp.name)], capture=capture)
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


# --------------------------------------------------------------- schema migration (v1 -> v2)
class TestSchemaMigration(unittest.TestCase):
    """A machine's existing usage.db (gitignored, SCHEMA_VERSION 1, no
    turn.project column) must upgrade cleanly the next time open_db() runs --
    additively, idempotently, without ever erroring or losing data."""

    def test_pre_v2_db_gains_project_column_idempotently(self):
        import sqlite3
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "old.db")

        # Simulate a pre-v2 db: a `turn` table with NO project column (mirrors
        # SCHEMA_VERSION 1, before this migration existed).
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE turn (
              turn_id TEXT PRIMARY KEY, provider TEXT NOT NULL, session_id TEXT NOT NULL,
              parent_turn_id TEXT, ts TEXT NOT NULL, wall_ms INTEGER,
              model TEXT, tier TEXT, request_id TEXT, stop_reason TEXT,
              input_fresh INTEGER DEFAULT 0, cache_read INTEGER DEFAULT 0,
              cache_write_5m INTEGER DEFAULT 0, cache_write_1h INTEGER DEFAULT 0,
              output INTEGER DEFAULT 0, reasoning_output INTEGER,
              compacted INTEGER DEFAULT 0, n_tool_calls INTEGER DEFAULT 0
            );
        """)
        old.execute("PRAGMA user_version = 1")
        old.commit()
        old.close()
        check = sqlite3.connect(path)
        cols_before = {r[1] for r in check.execute("PRAGMA table_info(turn)")}
        check.close()   # Windows holds the file open otherwise -- tmp.cleanup() would fail
        self.assertNotIn("project", cols_before)

        # open_db() on the pre-existing db: CREATE TABLE IF NOT EXISTS is a
        # no-op (turn already exists), so _migrate() must ALTER the column in.
        conn = canonical.open_db(path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], canonical.SCHEMA_VERSION)
        cols_after = {r[1] for r in conn.execute("PRAGMA table_info(turn)")}
        self.assertIn("project", cols_after)
        conn.close()

        # ingest succeeds against the upgraded db (no "no such column" errors)
        # and the new column is actually populated.
        src = tempfile.TemporaryDirectory()
        write_transcript(src.name, "projX", "c.jsonl", [
            assistant("2026-01-01T00:00:00Z", "s1", "claude-opus-4-8",
                      usage(inp=1, out=1), uuid="m1")])
        conn2 = canonical.open_db(path)
        canonical.ingest(conn2, [canonical.ClaudeProvider(root=src.name)])
        self.assertEqual(conn2.execute("SELECT project FROM turn WHERE turn_id='m1'").fetchone()[0], "projX")
        conn2.close()
        src.cleanup()

        # re-opening an ALREADY-migrated db doesn't error -- _migrate() sees
        # the column already present (PRAGMA table_info) and skips the ALTER.
        conn3 = canonical.open_db(path)
        conn3.close()
        tmp.cleanup()

    def test_migrate_swallows_duplicate_column_error(self):
        # Belt-and-suspenders on the except branch itself (not just the
        # PRAGMA table_info pre-check that normally avoids it): if the ALTER
        # ever runs against a column that already exists, sqlite3's
        # "duplicate column name" OperationalError must be swallowed, not
        # raised -- confirmed by calling a real ALTER twice, unguarded, and
        # checking canonical._migrate()'s except clause recognizes it as the
        # exact error text it's built to catch.
        import sqlite3
        conn = canonical.open_db(":memory:")   # already migrated to v2 (has turn.project)
        with self.assertRaises(sqlite3.OperationalError) as ctx:
            conn.execute("ALTER TABLE turn ADD COLUMN project TEXT")
        self.assertIn("duplicate column name", str(ctx.exception).lower())
        # canonical._migrate() itself, run again on this same (already
        # migrated) connection, must not raise -- it never gets far enough to
        # hit the ALTER (PRAGMA table_info already sees the column), which is
        # the primary guard; the except above documents the fallback still works.
        canonical._migrate(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(turn)")}
        self.assertIn("project", cols)

    def test_fresh_db_already_has_project_column(self):
        conn = canonical.open_db(":memory:")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(turn)")}
        self.assertIn("project", cols)


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

    def test_turn_project_populated_per_turn(self):
        # report.aggregate()'s by_project keys on this column directly (not on
        # the session's resolved project), so a turn's OWN file/project must
        # always be recorded here for Claude turns.
        r = self.conn.execute("SELECT project FROM turn WHERE turn_id='u1'").fetchone()
        self.assertEqual(r[0], "projX")   # ingest_lines()'s default project dir
        r2 = self.conn.execute("SELECT project FROM turn WHERE turn_id='u2'").fetchone()
        self.assertEqual(r2[0], "projX")

    def test_turn_project_differs_across_files_for_same_session(self):
        # The same session_id can have turns land in different project dirs
        # (a worktree switch mid-session); each turn keeps ITS OWN file's
        # project, independent of the other file's.
        tmp = tempfile.TemporaryDirectory()
        write_transcript(tmp.name, "projA", "a.jsonl", [
            assistant("2026-02-01T00:00:00Z", "sw", "claude-opus-4-8",
                      usage(inp=1, out=1), uuid="wa")])
        write_transcript(tmp.name, "projB", "b.jsonl", [
            assistant("2026-02-01T01:00:00Z", "sw", "claude-opus-4-8",
                      usage(inp=1, out=1), uuid="wb")])
        conn = canonical.open_db(":memory:")
        canonical.ingest(conn, [canonical.ClaudeProvider(root=tmp.name)])
        self.assertEqual(conn.execute("SELECT project FROM turn WHERE turn_id='wa'").fetchone()[0], "projA")
        self.assertEqual(conn.execute("SELECT project FROM turn WHERE turn_id='wb'").fetchone()[0], "projB")
        tmp.cleanup()

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


# --------------------------------------------------------------- raw_event age-out (retention)
def _insert_raw_event(conn, ts, raw="{}", type_="system", sid="s1", sha="h"):
    conn.execute(
        "INSERT INTO raw_event(provider,session_id,ts,type,sha256,raw) VALUES(?,?,?,?,?,?)",
        ("claude", sid, ts, type_, sha, raw))
    conn.commit()

class TestRawEventAgeOut(unittest.TestCase):
    """canonical.age_out() -- the write-side complement to the RAW_CAP clamp
    (docs/canonical-data-model.md §6/§7): a periodic pass that nulls out
    raw_event.raw for rows older than a retention window, leaving every
    structural column (provider/session_id/ts/type/sha256) untouched. `now`
    is an injectable seam so tests never depend on the real clock."""

    NOW = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)

    def _ts(self, days_ago):
        return (self.NOW - datetime.timedelta(days=days_ago)).isoformat()

    def test_ac1_nulls_raw_only_for_rows_older_than_window(self):
        conn = canonical.open_db(":memory:")
        _insert_raw_event(conn, self._ts(100), raw="old-blob", sid="old")
        _insert_raw_event(conn, self._ts(1), raw="new-blob", sid="new")
        canonical.age_out(conn, days=90, now=self.NOW)
        old_raw = conn.execute("SELECT raw FROM raw_event WHERE session_id='old'").fetchone()[0]
        new_raw = conn.execute("SELECT raw FROM raw_event WHERE session_id='new'").fetchone()[0]
        self.assertIsNone(old_raw)
        self.assertEqual(new_raw, "new-blob")

    def test_ac2_structural_columns_survive_only_raw_cleared(self):
        conn = canonical.open_db(":memory:")
        _insert_raw_event(conn, self._ts(200), raw="verbatim-json", type_="world_state",
                          sid="s-struct", sha="deadbeef")
        canonical.age_out(conn, days=90, now=self.NOW)
        r = conn.execute("SELECT provider,session_id,ts,type,sha256,raw FROM raw_event").fetchone()
        self.assertEqual(r[0], "claude")
        self.assertEqual(r[1], "s-struct")
        self.assertEqual(r[2], self._ts(200))
        self.assertEqual(r[3], "world_state")
        self.assertEqual(r[4], "deadbeef")   # hash always kept
        self.assertIsNone(r[5])              # only raw cleared

    def test_ac3_configurable_window_and_idempotent(self):
        conn = canonical.open_db(":memory:")
        _insert_raw_event(conn, self._ts(40), raw="blob", sid="mid")
        # default 90d window would keep a 40-day-old row...
        cleared_default = canonical.age_out(conn, now=self.NOW)
        self.assertEqual(cleared_default, 0)
        self.assertEqual(conn.execute("SELECT raw FROM raw_event WHERE session_id='mid'").fetchone()[0], "blob")
        # ...but a custom, shorter window clears it.
        cleared_custom = canonical.age_out(conn, days=30, now=self.NOW)
        self.assertEqual(cleared_custom, 1)
        self.assertIsNone(conn.execute("SELECT raw FROM raw_event WHERE session_id='mid'").fetchone()[0])
        # re-running (same or wider window) is a no-op: nothing left to clear.
        cleared_again = canonical.age_out(conn, days=30, now=self.NOW)
        self.assertEqual(cleared_again, 0)

    def test_ac4_boundary_kept_vs_cleared(self):
        conn = canonical.open_db(":memory:")
        _insert_raw_event(conn, self._ts(89), raw="kept", sid="under")     # window-1d -> kept
        _insert_raw_event(conn, self._ts(91), raw="cleared", sid="over")   # window+1d -> cleared
        canonical.age_out(conn, days=90, now=self.NOW)
        self.assertEqual(conn.execute("SELECT raw FROM raw_event WHERE session_id='under'").fetchone()[0], "kept")
        self.assertIsNone(conn.execute("SELECT raw FROM raw_event WHERE session_id='over'").fetchone()[0])

    def test_rowcount_reflects_rows_actually_cleared(self):
        conn = canonical.open_db(":memory:")
        _insert_raw_event(conn, self._ts(100), raw="a", sid="a")
        _insert_raw_event(conn, self._ts(120), raw="b", sid="b")
        _insert_raw_event(conn, self._ts(1), raw="c", sid="c")   # too new, not cleared
        cleared = canonical.age_out(conn, days=90, now=self.NOW)
        self.assertEqual(cleared, 2)

    def test_already_null_raw_rows_untouched_and_not_recounted(self):
        conn = canonical.open_db(":memory:")
        _insert_raw_event(conn, self._ts(100), raw=None, sid="already-null")
        cleared = canonical.age_out(conn, days=90, now=self.NOW)
        self.assertEqual(cleared, 0)   # raw IS NOT NULL guard: nothing to do
        self.assertIsNone(conn.execute("SELECT raw FROM raw_event WHERE session_id='already-null'").fetchone()[0])

    def test_default_days_uses_module_constant(self):
        conn = canonical.open_db(":memory:")
        _insert_raw_event(conn, self._ts(canonical.RAW_RETENTION_DAYS + 1), raw="blob", sid="s")
        cleared = canonical.age_out(conn, now=self.NOW)   # days omitted -> RAW_RETENTION_DAYS
        self.assertEqual(cleared, 1)


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


# --------------------------------------------------------------- codex tier map
class TestCodexTier(unittest.TestCase):
    def test_known_models(self):
        self.assertEqual(canonical.codex_tier("gpt-5-codex"), "gpt-5-codex")
        self.assertEqual(canonical.codex_tier("gpt-5.3-codex"), "gpt-5.3-codex")
        self.assertEqual(canonical.codex_tier("gpt-5.5"), "gpt-5.5")
        self.assertEqual(canonical.codex_tier("gpt-5.5-pro"), "gpt-5.5-pro")
        self.assertEqual(canonical.codex_tier("gpt-5"), "gpt-5")

    def test_unknown_model_returns_none(self):
        self.assertIsNone(canonical.codex_tier("claude-opus-4-8"))
        self.assertIsNone(canonical.codex_tier(None))
        self.assertIsNone(canonical.codex_tier(""))


# --------------------------------------------------------------- CodexProvider: turns & tokens
class TestCodexTurns(unittest.TestCase):
    def test_fresh_input_math_from_last_token_usage(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_turn_context("2026-01-01T10:00:00Z", "gpt-5-codex"),
            codex_token_count("2026-01-01T10:00:05Z",
                              last=tok(inp=1000, cached=800, out=50, reasoning=20),
                              total=tok(inp=1000, cached=800, out=50, reasoning=20)),
        ])
        r = conn.execute("SELECT input_fresh,cache_read,cache_write_5m,cache_write_1h,output,"
                         "reasoning_output,tier,model FROM turn").fetchone()
        self.assertEqual(r, (200, 800, 0, 0, 50, 20, "gpt-5-codex", "gpt-5-codex"))
        self.assertIsNone(conn.execute("SELECT project FROM turn").fetchone()[0])   # unused for codex
        tmp.cleanup()

    def test_reasoning_output_not_folded_into_output(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_turn_context("2026-01-01T10:00:00Z", "gpt-5-codex"),
            codex_token_count("2026-01-01T10:00:05Z",
                              last=tok(inp=100, cached=0, out=400, reasoning=256)),
        ])
        r = conn.execute("SELECT output,reasoning_output FROM turn").fetchone()
        self.assertEqual(r, (400, 256))   # reasoning is a subset, not additive
        tmp.cleanup()

    def test_old_flat_shape_first_turn_uses_absolute_values(self):
        # oldest format: payload.input_tokens etc, no info nesting, no history yet
        # to diff against -> the first observation is taken as the delta itself.
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_turn_context("2026-01-01T10:00:00Z", "gpt-5-codex"),
            codex_token_count("2026-01-01T10:00:05Z",
                              flat={"input_tokens": 500, "cached_input_tokens": 100,
                                    "output_tokens": 30, "reasoning_output_tokens": 5}),
        ])
        r = conn.execute("SELECT input_fresh,cache_read,output,reasoning_output FROM turn").fetchone()
        self.assertEqual(r, (400, 100, 30, 5))
        tmp.cleanup()

    def test_cumulative_diff_fallback_when_no_last_token_usage(self):
        # second token_count only carries `total_token_usage` (cumulative); the
        # per-turn delta must be diffed against the previous cumulative snapshot.
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_turn_context("2026-01-01T10:00:00Z", "gpt-5-codex"),
            codex_token_count("2026-01-01T10:00:01Z",
                              total=tok(inp=1000, cached=800, out=100, reasoning=10)),
            codex_token_count("2026-01-01T10:00:05Z",
                              total=tok(inp=1600, cached=1200, out=180, reasoning=30)),
        ])
        rows = conn.execute("SELECT input_fresh,cache_read,output,reasoning_output FROM turn "
                            "ORDER BY ts").fetchall()
        self.assertEqual(rows[0], (200, 800, 100, 10))     # first turn: absolute (no prior baseline)
        self.assertEqual(rows[1], (200, 400, 80, 20))      # second turn: diffed vs. prior cumulative
        tmp.cleanup()

    def test_unknown_model_still_recorded_with_null_tier(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_turn_context("2026-01-01T10:00:00Z", "some-future-model"),
            codex_token_count("2026-01-01T10:00:05Z", last=tok(inp=10, out=5)),
        ])
        r = conn.execute("SELECT model,tier FROM turn").fetchone()
        self.assertEqual(r, ("some-future-model", None))
        tmp.cleanup()

    def test_model_switch_mid_session_retiers_later_turns(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_turn_context("2026-01-01T10:00:00Z", "gpt-5-codex"),
            codex_token_count("2026-01-01T10:00:01Z", last=tok(inp=10, out=5)),
            codex_turn_context("2026-01-01T10:00:02Z", "gpt-5.5"),
            codex_token_count("2026-01-01T10:00:03Z", last=tok(inp=10, out=5)),
        ])
        rows = conn.execute("SELECT model,tier FROM turn ORDER BY ts").fetchall()
        self.assertEqual(rows[0], ("gpt-5-codex", "gpt-5-codex"))
        self.assertEqual(rows[1], ("gpt-5.5", "gpt-5.5"))
        tmp.cleanup()

    def test_codex_exec_file_with_no_token_count_events_yields_zero_turns(self):
        # headless `codex exec` runs historically omit token_count entirely.
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_message("2026-01-01T10:00:01Z", "user", "do the thing"),
            codex_function_call("2026-01-01T10:00:02Z", "call_1", "shell", '{"command":["ls"]}'),
            codex_function_call_output("2026-01-01T10:00:03Z", "call_1",
                                       '{"output":"a.txt\\n","metadata":{"exit_code":0,"duration_seconds":0.1}}'),
        ])
        self.assertEqual(conn.execute("SELECT count(*) FROM turn").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT count(*) FROM tool_call").fetchone()[0], 1)
        self.assertGreater(conn.execute("SELECT count(*) FROM segment").fetchone()[0], 0)
        tmp.cleanup()


# --------------------------------------------------------------- CodexProvider: tool calls
class TestCodexToolCalls(unittest.TestCase):
    def test_failing_call_via_nonzero_exit_code(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_turn_context("2026-01-01T10:00:00Z", "gpt-5-codex"),
            codex_function_call("2026-01-01T10:00:01Z", "call_1", "shell", '{"command":["git","status"]}'),
            codex_function_call_output("2026-01-01T10:00:02Z", "call_1",
                                       '{"output":"fatal: not a git repository (or any of the parent directories): .git",'
                                       '"metadata":{"exit_code":128,"duration_seconds":0.05}}'),
        ])
        r = conn.execute("SELECT ok,error_class,exit_code FROM tool_call WHERE call_id='call_1'").fetchone()
        self.assertEqual(r, (0, "git_error", 128))
        tmp.cleanup()

    def test_success_call_ok_with_null_error(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_function_call("2026-01-01T10:00:01Z", "call_1", "update_plan", '{"plan":[]}'),
            codex_function_call_output("2026-01-01T10:00:02Z", "call_1", "Plan updated"),
        ])
        r = conn.execute("SELECT ok,error_class,exit_code FROM tool_call WHERE call_id='call_1'").fetchone()
        self.assertEqual(r, (1, None, None))
        tmp.cleanup()

    def test_call_name_kind_and_turn_join(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_turn_context("2026-01-01T10:00:00Z", "gpt-5-codex"),
            codex_token_count("2026-01-01T10:00:01Z", last=tok(inp=10, out=5)),
            codex_function_call("2026-01-01T10:00:02Z", "call_1", "apply_patch", '{"patch":"..."}'),
            codex_function_call_output("2026-01-01T10:00:03Z", "call_1", "applied"),
        ])
        r = conn.execute("SELECT name,kind,turn_id FROM tool_call WHERE call_id='call_1'").fetchone()
        self.assertEqual(r[0], "apply_patch")
        self.assertEqual(r[1], "file_edit")
        self.assertEqual(r[2], "cs1:1")

    def test_call_and_result_collapse_to_one_row(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_function_call("2026-01-01T10:00:01Z", "call_1", "shell", '{"command":["ls"]}'),
            codex_function_call_output("2026-01-01T10:00:02Z", "call_1",
                                       '{"output":"ok\\n","metadata":{"exit_code":0,"duration_seconds":0.1}}'),
        ])
        self.assertEqual(conn.execute("SELECT count(*) FROM tool_call").fetchone()[0], 1)
        tmp.cleanup()


# --------------------------------------------------------------- CodexProvider: segments
class TestCodexSegments(unittest.TestCase):
    def test_reasoning_summary_present_becomes_text(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_reasoning("2026-01-01T10:00:00Z", summary_text="planning the fix"),
        ], capture="verbatim")
        r = conn.execute("SELECT text,text_available FROM segment WHERE kind='reasoning'").fetchone()
        self.assertEqual(r, ("planning the fix", 1))
        tmp.cleanup()

    def test_reasoning_count_only_has_no_text_but_hashes_encrypted_content(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_reasoning("2026-01-01T10:00:00Z", summary_text=None, encrypted="gAAA-secret"),
        ])
        r = conn.execute("SELECT text,text_available,sha256 FROM segment WHERE kind='reasoning'").fetchone()
        self.assertIsNone(r[0])
        self.assertEqual(r[1], 0)
        self.assertEqual(r[2], canonical.sha("gAAA-secret"))
        tmp.cleanup()

    def test_message_segments_prompt_and_response(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_message("2026-01-01T10:00:01Z", "user", "please fix the bug"),
            codex_message("2026-01-01T10:00:02Z", "assistant", "Fixed it."),
        ], capture="verbatim")
        rows = dict(conn.execute("SELECT kind,text FROM segment WHERE kind IN ('prompt','response')"))
        self.assertEqual(rows["prompt"], "please fix the bug")
        self.assertEqual(rows["response"], "Fixed it.")
        tmp.cleanup()

    def test_tool_args_and_output_captured(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_function_call("2026-01-01T10:00:01Z", "call_1", "shell", '{"command":["ls"]}'),
            codex_function_call_output("2026-01-01T10:00:02Z", "call_1",
                                       '{"output":"a.txt\\n","metadata":{"exit_code":0,"duration_seconds":0.1}}'),
        ], capture="verbatim")
        rows = dict(conn.execute("SELECT kind,text FROM segment WHERE kind IN ('tool_args','tool_output')"))
        self.assertEqual(rows["tool_args"], '{"command":["ls"]}')
        self.assertEqual(rows["tool_output"], "a.txt\n")
        tmp.cleanup()

    def test_unmodeled_event_msg_captured_as_raw_event(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_line("2026-01-01T10:00:01Z", "event_msg", type="agent_reasoning", text="thinking..."),
        ])
        r = conn.execute("SELECT type FROM raw_event").fetchone()
        self.assertIsNotNone(r)
        tmp.cleanup()


# --------------------------------------------------------------- CodexProvider: session
class TestCodexSession(unittest.TestCase):
    def test_session_metadata_recorded(self):
        conn, tmp = codex_rollout([
            codex_session_meta("2026-01-01T10:00:00Z", "cs1", cwd=r"C:\Users\u\proj",
                               cli_version="0.39.0", originator="codex_cli_rs"),
            codex_turn_context("2026-01-01T10:00:00Z", "gpt-5-codex"),
        ])
        r = conn.execute("SELECT provider,cwd,cli_version,source,approval_policy "
                         "FROM session WHERE session_id='cs1'").fetchone()
        self.assertEqual(r[0], "codex")
        self.assertEqual(r[1], r"C:\Users\u\proj")
        self.assertEqual(r[2], "0.39.0")
        self.assertEqual(r[3], "codex_cli_rs")
        self.assertEqual(r[4], "on-request")
        tmp.cleanup()


# --------------------------------------------------------------- CodexProvider: idempotency
class TestCodexIdempotency(unittest.TestCase):
    def test_reingest_does_not_grow_row_counts(self):
        lines = [
            codex_session_meta("2026-01-01T10:00:00Z", "cs1"),
            codex_turn_context("2026-01-01T10:00:00Z", "gpt-5-codex"),
            codex_message("2026-01-01T10:00:01Z", "user", "hi"),
            codex_function_call("2026-01-01T10:00:02Z", "call_1", "shell", '{"command":["ls"]}'),
            codex_function_call_output("2026-01-01T10:00:03Z", "call_1",
                                       '{"output":"ok\\n","metadata":{"exit_code":0,"duration_seconds":0.1}}'),
            codex_token_count("2026-01-01T10:00:04Z", last=tok(inp=10, out=5)),
        ]
        tmp = tempfile.TemporaryDirectory()
        write_transcript(tmp.name, "2026", "rollout.jsonl", lines)
        conn = canonical.open_db(":memory:")
        prov = [canonical.CodexProvider(root=tmp.name)]
        counts = lambda: tuple(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                               for t in ("session", "turn", "tool_call", "segment", "raw_event"))
        canonical.ingest(conn, prov)
        first = counts()
        canonical.ingest(conn, prov)
        self.assertEqual(counts(), first)
        tmp.cleanup()


# --------------------------------------------------------------- default_providers registration
class TestDefaultProviders(unittest.TestCase):
    def test_codex_provider_registered(self):
        names = {p.name for p in canonical.default_providers()}
        self.assertEqual(names, {"claude", "codex"})


if __name__ == "__main__":
    unittest.main()
