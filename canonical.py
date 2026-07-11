#!/usr/bin/env python3
"""
Canonical usage store — a provider-neutral, turn/tool-grained SQLite database
that captures far more than the daily/session aggregates report.py emits.

Design: docs/canonical-data-model.md. In one line: the raw transcripts rotate
away and the day/session JSON is lossy, so this layer records every assistant
*turn*, every *tool call*, and (optionally) the *content* of prompts/responses/
reasoning/tool-io into a durable, only-grows db that downstream analysis can
query for questions we haven't thought of yet.

The abstraction is a producer/consumer split:
  * a Provider *produces* a stream of neutral Rec dataclasses from its own log
    format (ClaudeProvider here; CodexProvider is the planned twin),
  * write() *consumes* that stream and upserts it into the schema, blind to
    which provider produced it.
Adding a provider = adding one Provider class. Nothing else changes.

Stdlib only (sqlite3). Runs alongside the existing pipeline (dual-write); it
never mutates report.py's stores. Privacy: TRACEYIELD_CAPTURE=verbatim stores
raw text; the default ("structural") stores only length + sha256.
"""
import os, glob, json, hashlib, sqlite3, datetime
from dataclasses import dataclass
import report   # reuse tier(), classify(), result_text(), project_of(), machine_id()

# ---------------------------------------------------------------- config
CAPTURE = os.environ.get("TRACEYIELD_CAPTURE") or "structural"   # "structural" | "verbatim"
RAW_CAP = 32 * 1024      # max bytes of raw_event.raw kept in verbatim mode (§7)
RAW_RETENTION_DAYS = int(os.environ.get("TRACEYIELD_RAW_RETENTION_DAYS") or 90)   # age_out() window (§7)
SCHEMA_VERSION = 2   # v2: turn.project (per-turn project, see MIGRATIONS)
DB_FILE = os.path.join(report.MACHINE_DIR, "usage.db")

# ---------------------------------------------------------------- schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
  provider TEXT NOT NULL, session_id TEXT NOT NULL, machine_id TEXT,
  project TEXT, cwd TEXT, git_branch TEXT, cli_version TEXT, source TEXT,
  approval_policy TEXT, sandbox_policy TEXT, first_ts TEXT, last_ts TEXT,
  PRIMARY KEY (provider, session_id)
);
CREATE TABLE IF NOT EXISTS turn (
  turn_id TEXT PRIMARY KEY, provider TEXT NOT NULL, session_id TEXT NOT NULL,
  parent_turn_id TEXT, ts TEXT NOT NULL, wall_ms INTEGER,
  model TEXT, tier TEXT, request_id TEXT, stop_reason TEXT,
  input_fresh INTEGER DEFAULT 0, cache_read INTEGER DEFAULT 0,
  cache_write_5m INTEGER DEFAULT 0, cache_write_1h INTEGER DEFAULT 0,
  output INTEGER DEFAULT 0, reasoning_output INTEGER,
  compacted INTEGER DEFAULT 0, n_tool_calls INTEGER DEFAULT 0,
  project TEXT
);
CREATE TABLE IF NOT EXISTS tool_call (
  call_id TEXT PRIMARY KEY, provider TEXT NOT NULL, session_id TEXT,
  turn_id TEXT, ts TEXT, name TEXT, kind TEXT,
  ok INTEGER, error_class TEXT, exit_code INTEGER,
  output_bytes INTEGER, latency_ms INTEGER
);
-- turn_id/tool_call_id use '' (not NULL) for the unused side so the UNIQUE key
-- dedupes on re-ingest (SQLite treats NULLs as distinct, which would not).
CREATE TABLE IF NOT EXISTS segment (
  segment_id INTEGER PRIMARY KEY,
  turn_id TEXT NOT NULL DEFAULT '', tool_call_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0, role TEXT,
  length INTEGER, sha256 TEXT, text TEXT, text_available INTEGER DEFAULT 1,
  UNIQUE (turn_id, tool_call_id, kind, seq)
);
CREATE TABLE IF NOT EXISTS raw_event (
  provider TEXT, session_id TEXT, ts TEXT, type TEXT, sha256 TEXT, raw TEXT,
  UNIQUE (provider, session_id, ts, type, sha256)
);
CREATE INDEX IF NOT EXISTS turn_day  ON turn(substr(ts,1,10));
CREATE INDEX IF NOT EXISTS turn_sess ON turn(provider, session_id);
CREATE INDEX IF NOT EXISTS tool_turn ON tool_call(turn_id);
CREATE INDEX IF NOT EXISTS seg_turn  ON segment(turn_id);
CREATE INDEX IF NOT EXISTS seg_tool  ON segment(tool_call_id);
"""

# Additive-only column migrations (docs/canonical-data-model.md §8): each entry
# is (table, column, type-and-default DDL fragment) added to an EXISTING db
# that predates it. CREATE TABLE IF NOT EXISTS only helps a brand-new db (it's
# a no-op on a table that already exists), so a column added to SCHEMA above
# also needs its ALTER TABLE listed here to reach machines with an old
# usage.db. Order matters only in that later entries may assume earlier ones.
MIGRATIONS = [
    ("turn", "project", "TEXT"),   # v2: per-turn project (report.aggregate() by_project)
]

def _migrate(conn):
    """Idempotently apply MIGRATIONS to an already-open connection. Safe to run
    on every open_db() call: skips a column that's already present (checked via
    PRAGMA table_info, and belt-and-suspenders around the duplicate-column
    OperationalError), so a fresh db and a repeatedly-opened old db both end up
    identical and no ALTER ever runs twice."""
    for table, column, ddl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

def open_db(path=DB_FILE):
    """Open (creating if needed) the canonical db and ensure the schema exists.

    A brand-new db gets the full current SCHEMA (already includes every
    migrated column). An EXISTING db (older SCHEMA_VERSION, gitignored
    machines/<id>/usage.db) gets its CREATE TABLE IF NOT EXISTS statements
    skipped for tables that already exist, so _migrate() additively ALTERs in
    any columns it's missing -- this must run on every open, not just once,
    so an old db upgrades cleanly the next time a machine runs."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn

# ---------------------------------------------------------------- neutral records
@dataclass
class Session:
    provider: str; session_id: str
    project: str = None; cwd: str = None; git_branch: str = None
    cli_version: str = None; source: str = None
    approval_policy: str = None; sandbox_policy: str = None
    first_ts: str = None; last_ts: str = None

@dataclass
class Turn:
    provider: str; session_id: str; turn_id: str; ts: str; model: str
    parent_turn_id: str = None; request_id: str = None; stop_reason: str = None
    input_fresh: int = 0; cache_read: int = 0; cache_write_5m: int = 0
    cache_write_1h: int = 0; output: int = 0; reasoning_output: int = None
    compacted: bool = False; n_tool_calls: int = 0; wall_ms: int = None
    tier: str = None
    project: str = None   # the project the SOURCE FILE lives in (per-turn, not
                           # per-session -- a session can span project dirs)

@dataclass
class ToolCall:
    provider: str; session_id: str; call_id: str; turn_id: str; ts: str
    name: str = None; kind: str = None; ok: bool = None; error_class: str = None
    exit_code: int = None; output_bytes: int = 0; latency_ms: int = None

@dataclass
class Segment:
    kind: str; role: str = None; turn_id: str = None; tool_call_id: str = None
    seq: int = 0; text: str = None; text_available: bool = True
    hash_src: str = None    # hashed for provenance when text is absent (e.g. redacted reasoning signature)

@dataclass
class RawEvent:
    provider: str; session_id: str; ts: str; type: str; raw: str = None

# ---------------------------------------------------------------- shared helpers
def sha(s):
    if s is None: return None
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()

def _ms(ts):
    """ISO-8601 → epoch milliseconds (for wall/latency deltas); None on junk."""
    if not ts: return None
    try:
        return int(datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None

def _b(x):
    return None if x is None else int(bool(x))

# Raw tool name (either provider) → normalized kind. Cross-provider so tool
# analysis works regardless of harness (§4.2). Codex names included for the
# planned second provider even though ClaudeProvider won't emit them.
TOOL_KIND = {
    "edit": "file_edit", "write": "file_edit", "multiedit": "file_edit",
    "notebookedit": "file_edit", "apply_patch": "file_edit",
    "read": "file_read", "notebookread": "file_read", "read_file": "file_read",
    "bash": "shell", "shell": "shell", "exec_command": "shell", "local_shell_call": "shell",
    "grep": "search", "glob": "search",
    "todowrite": "plan", "exitplanmode": "plan", "update_plan": "plan",
    "webfetch": "web", "websearch": "web",
    "task": "agent", "agent": "agent",
}
def tool_kind(name):
    if not name: return None
    n = name.lower()
    if n.startswith("mcp__"): return "mcp"
    return TOOL_KIND.get(n, "other")

# ---------------------------------------------------------------- consumer (provider-blind)
def write(conn, rec, verbatim):
    """Upsert one neutral Rec into the schema. Idempotent by natural key."""
    if isinstance(rec, Turn):
        conn.execute(
            "INSERT OR IGNORE INTO turn(turn_id,provider,session_id,parent_turn_id,ts,wall_ms,"
            "model,tier,request_id,stop_reason,input_fresh,cache_read,cache_write_5m,cache_write_1h,"
            "output,reasoning_output,compacted,n_tool_calls,project) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.turn_id, rec.provider, rec.session_id, rec.parent_turn_id, rec.ts, rec.wall_ms,
             rec.model, rec.tier, rec.request_id, rec.stop_reason, rec.input_fresh, rec.cache_read,
             rec.cache_write_5m, rec.cache_write_1h, rec.output, rec.reasoning_output,
             _b(rec.compacted), rec.n_tool_calls, rec.project))
    elif isinstance(rec, ToolCall):
        # ts tracks the LATEST known event for this call (call issue or result
        # return, whichever is later) — same MAX-with-COALESCE idiom as
        # session.last_ts below. This matters for report.aggregate(): a
        # session's activity span is derived from turn+tool_call ts (§ report.py
        # aggregate()), and a call's result line is often the true last touch.
        if rec.name is not None:            # the call itself
            conn.execute(
                "INSERT INTO tool_call(call_id,provider,session_id,turn_id,ts,name,kind) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET "
                "name=excluded.name, kind=excluded.kind, "
                "turn_id=COALESCE(tool_call.turn_id, excluded.turn_id), "
                "ts=MAX(COALESCE(tool_call.ts,excluded.ts),COALESCE(excluded.ts,tool_call.ts))",
                (rec.call_id, rec.provider, rec.session_id, rec.turn_id, rec.ts, rec.name, rec.kind))
        else:                                # the result (ok/error/latency), joined by call_id
            conn.execute(
                "INSERT INTO tool_call(call_id,provider,session_id,turn_id,ts,ok,error_class,"
                "exit_code,output_bytes,latency_ms) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(call_id) DO UPDATE SET ok=excluded.ok, error_class=excluded.error_class, "
                "exit_code=COALESCE(excluded.exit_code, tool_call.exit_code), "
                "output_bytes=excluded.output_bytes, latency_ms=excluded.latency_ms, "
                "turn_id=COALESCE(tool_call.turn_id, excluded.turn_id), "
                "ts=MAX(COALESCE(tool_call.ts,excluded.ts),COALESCE(excluded.ts,tool_call.ts))",
                (rec.call_id, rec.provider, rec.session_id, rec.turn_id, rec.ts, _b(rec.ok),
                 rec.error_class, rec.exit_code, rec.output_bytes, rec.latency_ms))
    elif isinstance(rec, Segment):
        src = rec.text if rec.text is not None else rec.hash_src   # hash content even in structural mode
        conn.execute(
            "INSERT OR IGNORE INTO segment(turn_id,tool_call_id,kind,seq,role,length,sha256,"
            "text,text_available) VALUES(?,?,?,?,?,?,?,?,?)",
            (rec.turn_id or "", rec.tool_call_id or "", rec.kind, rec.seq, rec.role,
             len(rec.text or ""), sha(src), (rec.text if verbatim else None), _b(rec.text_available)))
    elif isinstance(rec, RawEvent):
        raw = rec.raw[:RAW_CAP] if (verbatim and rec.raw is not None) else None   # §7 cap
        conn.execute(
            "INSERT OR IGNORE INTO raw_event(provider,session_id,ts,type,sha256,raw) "
            "VALUES(?,?,?,?,?,?)",
            (rec.provider, rec.session_id, rec.ts, rec.type, sha(rec.raw), raw))
    elif isinstance(rec, Session):
        # project/cwd/git_branch/cli_version are FIRST-wins (keep the already-
        # stored value when present) to match analyze()'s first-seen-wins
        # semantics for a session's project/meta ("if S['project'] is None:
        # S['project']=proj"; meta uses "if 'cwd' not in meta"). A session
        # whose cwd/project changed mid-conversation (e.g. a worktree switch)
        # must resolve to its FIRST project, not whichever file ingest()
        # happens to process last. first_ts/last_ts stay MIN/MAX (already
        # order-independent).
        conn.execute(
            "INSERT INTO session(provider,session_id,machine_id,project,cwd,git_branch,cli_version,"
            "source,approval_policy,sandbox_policy,first_ts,last_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(provider,session_id) DO UPDATE SET "
            "project=COALESCE(session.project, excluded.project), "
            "cwd=COALESCE(session.cwd, excluded.cwd), "
            "git_branch=COALESCE(session.git_branch, excluded.git_branch), "
            "cli_version=COALESCE(session.cli_version, excluded.cli_version), "
            "first_ts=MIN(COALESCE(session.first_ts,excluded.first_ts),COALESCE(excluded.first_ts,session.first_ts)), "
            "last_ts=MAX(COALESCE(session.last_ts,excluded.last_ts),COALESCE(excluded.last_ts,session.last_ts))",
            (rec.provider, rec.session_id, report.machine_id(), rec.project, rec.cwd, rec.git_branch,
             rec.cli_version, rec.source, rec.approval_policy, rec.sandbox_policy,
             rec.first_ts, rec.last_ts))

# ---------------------------------------------------------------- producers (providers)
def _iter_json_lines(path):
    try:
        fh = open(path, encoding="utf-8")
    except Exception:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

class ClaudeProvider:
    """Produces neutral Recs from Claude Code transcripts (~/.claude/projects)."""
    name = "claude"

    def __init__(self, root=None):
        self.root = root or report.CLAUDE_PROJECTS

    def roots(self):
        return [self.root]

    def parse_file(self, path):
        proj = report.project_of(path, self.root)
        sid = None
        meta = {}                 # cwd / git / version, first seen wins (file-level)
        idmeta = {}               # tool_use.id -> (turn_id, call_ts_ms) for the result join
        prev_ms = {}              # sessionId -> last turn ts (ms), for wall_ms
        spans = {}                 # sessionId -> [first_ts, last_ts] — PER SESSION, not per
                                    # file: one file can hold multiple sessions (a rotated/
                                    # resumed conversation), so a single file-wide span would
                                    # bleed one session's timestamps into another's.
        seq = 0
        for o in _iter_json_lines(path):
            ts = o.get("timestamp")
            if o.get("sessionId"): sid = o.get("sessionId")
            if ts and sid:
                sp = spans.get(sid)
                if sp is None: spans[sid] = [ts, ts]
                else:
                    if ts < sp[0]: sp[0] = ts
                    if ts > sp[1]: sp[1] = ts
            if o.get("cwd") and "cwd" not in meta: meta["cwd"] = o.get("cwd")
            if o.get("gitBranch") and "git" not in meta: meta["git"] = o.get("gitBranch")
            if o.get("version") and "ver" not in meta: meta["ver"] = o.get("version")

            m = o.get("message")
            if not isinstance(m, dict):
                if ts:                                     # unmodeled line → escape hatch
                    yield RawEvent("claude", sid, ts, o.get("type", "?"), json.dumps(o))
                continue

            content = m.get("content")
            content = content if isinstance(content, list) else []
            u = m.get("usage")

            if isinstance(u, dict):                        # an assistant turn (billable)
                seq += 1
                tid = o.get("uuid") or f"{sid}:{seq}"
                inp = u.get("input_tokens", 0) or 0
                out = u.get("output_tokens", 0) or 0
                cr = u.get("cache_read_input_tokens", 0) or 0
                cc = u.get("cache_creation_input_tokens", 0) or 0
                det = u.get("cache_creation") or {}
                w1h = det.get("ephemeral_1h_input_tokens", 0) or 0
                w5m = det.get("ephemeral_5m_input_tokens", 0) or 0
                if w1h + w5m == 0 and cc > 0: w5m = cc     # aggregate → 5m fallback
                cur = _ms(ts)
                wall = (cur - prev_ms[sid]) if (sid in prev_ms and cur is not None and prev_ms[sid] is not None) else None
                prev_ms[sid] = cur
                n_tools = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use")
                yield Turn("claude", sid, tid, ts, m.get("model") or "",
                           parent_turn_id=o.get("parentUuid"), request_id=o.get("requestId"),
                           stop_reason=m.get("stop_reason"), input_fresh=inp, cache_read=cr,
                           cache_write_5m=w5m, cache_write_1h=w1h, output=out,
                           reasoning_output=None,   # Claude has no separate reasoning count (§2.4)
                           n_tool_calls=n_tools, wall_ms=wall, tier=report.tier(m.get("model")),
                           project=proj)   # the FILE's own project, not the session's resolved one
                for i, b in enumerate(content):
                    if not isinstance(b, dict): continue
                    t = b.get("type")
                    if t == "text":
                        yield Segment("response", "assistant", turn_id=tid, seq=i, text=b.get("text") or "")
                    elif t == "thinking":
                        think = b.get("thinking") or ""    # redacted to "" in practice (§2.4)
                        yield Segment("reasoning", "assistant", turn_id=tid, seq=i,
                                      text=(think or None), text_available=bool(think),
                                      hash_src=b.get("signature"))
                    elif t == "tool_use":
                        cid = b.get("id"); nm = b.get("name") or "?"
                        idmeta[cid] = (tid, cur)
                        yield ToolCall("claude", sid, cid, tid, ts, name=nm, kind=tool_kind(nm))
                        yield Segment("tool_args", "tool", tool_call_id=cid, text=json.dumps(b.get("input", {})))
            else:                                          # a user line: prompts and/or tool_results
                if isinstance(m.get("content"), str) and m.get("role") == "user":
                    yield Segment("prompt", "user", turn_id=(o.get("uuid") or ""), text=m.get("content"))
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_result": continue
                    cid = b.get("tool_use_id"); txt = report.result_text(b)
                    is_err = bool(b.get("is_error"))
                    tm = idmeta.get(cid)
                    cur = _ms(ts)
                    lat = (cur - tm[1]) if (tm and cur is not None and tm[1] is not None) else None
                    yield ToolCall("claude", sid, cid, tm[0] if tm else None, ts, name=None,
                                   ok=(not is_err), error_class=(report.classify(txt) if is_err else None),
                                   output_bytes=len(txt), latency_ms=lat)
                    yield Segment("tool_output", "tool", tool_call_id=cid, text=txt)

        for s, (f_ts, l_ts) in spans.items():   # one Session row per distinct session_id
            yield Session("claude", s, project=proj, cwd=meta.get("cwd"),
                          git_branch=meta.get("git"), cli_version=meta.get("ver"),
                          first_ts=f_ts, last_ts=l_ts)

# Codex model family → tier label. NOT report.tier() (that's Claude-only:
# opus/sonnet/haiku). Unknown model -> None, but the turn is still recorded
# (raw model id never lost — same policy as ClaudeProvider/report.tier()).
CODEX_TIER = {
    "gpt-5-codex": "gpt-5-codex",
    "gpt-5.3-codex": "gpt-5.3-codex",
    "gpt-5.5": "gpt-5.5",
    "gpt-5.5-pro": "gpt-5.5-pro",
    "gpt-5": "gpt-5",
}
def codex_tier(model):
    if not model: return None
    m = model.lower()
    if m in CODEX_TIER: return CODEX_TIER[m]
    # forward-tolerant: an unseen gpt-5* id (new dot-revision/-codex variant) is
    # still a recognizable family member — tier it by its own id rather than
    # dropping to None, so the tier map doesn't need an edit for every point
    # release. Truly unrelated models (a different vendor/family) → None.
    if m.startswith("gpt-5"): return m
    return None

def _codex_text(content):
    """Join text parts of a response_item message content list."""
    out = []
    for b in content or []:
        if isinstance(b, dict) and isinstance(b.get("text"), str):
            out.append(b["text"])
    return "".join(out)

def _codex_tool_output(raw):
    """Parse a function_call_output's `output` field.

    Usually a JSON string {"output": "<text>", "metadata": {"exit_code":...}}.
    Sometimes a structured {"content":..., "success": bool}. Sometimes plain
    text. Returns (text, exit_code, success) with success=None when unknown.
    """
    if not isinstance(raw, str):
        return ("" if raw is None else str(raw)), None, None
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw, None, None
    if isinstance(parsed, dict):
        if "output" in parsed:
            text = parsed.get("output")
            text = text if isinstance(text, str) else json.dumps(text)
            meta = parsed.get("metadata") or {}
            exit_code = meta.get("exit_code") if isinstance(meta, dict) else None
            success = parsed.get("success")
            return text, exit_code, success
        if "content" in parsed or "success" in parsed:
            text = parsed.get("content")
            text = text if isinstance(text, str) else json.dumps(text)
            return text, None, parsed.get("success")
    return raw, None, None

class CodexProvider:
    """Produces neutral Recs from Codex CLI rollout logs (~/.codex/sessions)."""
    name = "codex"

    def __init__(self, root=None):
        self.root = root or os.environ.get("CODEX_HOME") or os.path.expanduser(r"~/.codex/sessions")

    def roots(self):
        return [self.root]

    def parse_file(self, path):
        sid = None
        meta = {}                 # cwd / cli_version / source, first seen wins
        approval_policy = sandbox_policy = None
        model = None               # active model from the most-recent turn_context
        call_meta = {}             # call_id -> (turn_id, ts_ms)
        cur_turn_id = ""          # most-recent synthesized turn_id (for tool_args w/o a turn yet)
        prev_total = None          # last seen cumulative total_token_usage, for the diff fallback
        pending_compacted = False  # set True after a `compacted` line; consumed by next Turn
        first_ts = last_ts = None
        seq = 0            # counts synthesized turns (for turn_id)
        line_seq = 0        # monotonic per-line counter for segment ordering/uniqueness

        for o in _iter_json_lines(path):
            line_seq += 1
            ts = o.get("timestamp")
            if ts:
                if first_ts is None or ts < first_ts: first_ts = ts
                if last_ts is None or ts > last_ts: last_ts = ts
            t = o.get("type")
            p = o.get("payload")
            p = p if isinstance(p, dict) else {}

            if t == "session_meta":
                sid = p.get("id") or sid
                if p.get("cwd") and "cwd" not in meta: meta["cwd"] = p.get("cwd")
                if p.get("cli_version") and "ver" not in meta: meta["ver"] = p.get("cli_version")
                # tolerate old/new naming: originator (real data) vs source/model_provider
                src = p.get("originator") or p.get("source") or p.get("model_provider")
                if src and "src" not in meta: meta["src"] = src
                continue

            if t == "turn_context":
                model = p.get("model") or model
                approval_policy = p.get("approval_policy") or approval_policy
                sp = p.get("sandbox_policy")
                if sp is not None:
                    sandbox_policy = sp if isinstance(sp, str) else json.dumps(sp)
                continue

            if t == "compacted":
                pending_compacted = True
                continue

            pt = p.get("type")

            if t == "event_msg" and pt == "token_count":
                info = p.get("info")
                if isinstance(info, dict):
                    last = info.get("last_token_usage")
                    total = info.get("total_token_usage")
                    if isinstance(last, dict):
                        delta = last
                    elif isinstance(total, dict):
                        if prev_total is not None:
                            delta = {k: (total.get(k, 0) or 0) - (prev_total.get(k, 0) or 0)
                                     for k in ("input_tokens", "cached_input_tokens",
                                               "output_tokens", "reasoning_output_tokens")}
                        else:
                            delta = total
                    else:
                        delta = None
                    if isinstance(total, dict):
                        prev_total = total
                else:
                    # old flat shape: payload.input_tokens etc, no info nesting
                    flat = {k: p.get(k) for k in ("input_tokens", "cached_input_tokens",
                                                   "output_tokens", "reasoning_output_tokens")
                            if k in p}
                    if flat:
                        if prev_total is not None:
                            delta = {k: (flat.get(k, 0) or 0) - (prev_total.get(k, 0) or 0)
                                     for k in ("input_tokens", "cached_input_tokens",
                                               "output_tokens", "reasoning_output_tokens")}
                        else:
                            delta = flat
                        prev_total = flat
                    else:
                        delta = None
                if delta is not None:
                    seq += 1
                    tid = f"{sid}:{seq}"
                    cur_turn_id = tid
                    inp = delta.get("input_tokens", 0) or 0
                    cached = delta.get("cached_input_tokens", 0) or 0
                    out = delta.get("output_tokens", 0) or 0
                    reasoning = delta.get("reasoning_output_tokens", 0) or 0
                    yield Turn("codex", sid, tid, ts, model or "",
                               input_fresh=max(inp - cached, 0), cache_read=cached,
                               cache_write_5m=0, cache_write_1h=0, output=out,
                               reasoning_output=reasoning, compacted=pending_compacted,
                               tier=codex_tier(model),
                               project=None)   # unused: report.aggregate() scopes to provider='claude'
                    pending_compacted = False
                continue

            if t == "response_item" and pt == "message":
                role = p.get("role")
                text = _codex_text(p.get("content"))
                if role == "assistant":
                    yield Segment("response", "assistant", turn_id=cur_turn_id, seq=line_seq, text=text)
                elif role == "user":
                    yield Segment("prompt", "user", turn_id=cur_turn_id, seq=line_seq, text=text)
                continue

            if t == "response_item" and pt == "reasoning":
                summary = p.get("summary")
                summary_text = None
                if isinstance(summary, list) and summary:
                    parts = [s.get("text", "") for s in summary if isinstance(s, dict)]
                    summary_text = "".join(parts) or None
                yield Segment("reasoning", "assistant", turn_id=cur_turn_id, seq=line_seq,
                              text=summary_text, text_available=bool(summary_text),
                              hash_src=p.get("encrypted_content"))
                continue

            if t == "response_item" and pt == "function_call":
                cid = p.get("call_id") or "?"
                nm = p.get("name") or "?"
                cur = _ms(ts)
                call_meta[cid] = (cur_turn_id, cur)
                yield ToolCall("codex", sid, cid, cur_turn_id, ts, name=nm, kind=tool_kind(nm))
                yield Segment("tool_args", "tool", tool_call_id=cid, text=p.get("arguments") or "")
                continue

            if t == "response_item" and pt == "function_call_output":
                cid = p.get("call_id") or "?"
                text, exit_code, success = _codex_tool_output(p.get("output"))
                failed = (exit_code not in (None, 0)) or (success is False)
                tm = call_meta.get(cid)
                cur = _ms(ts)
                lat = (cur - tm[1]) if (tm and cur is not None and tm[1] is not None) else None
                yield ToolCall("codex", sid, cid, tm[0] if tm else None, ts, name=None,
                               ok=(not failed), error_class=(report.classify(text) if failed else None),
                               exit_code=exit_code, output_bytes=len(text or ""), latency_ms=lat)
                yield Segment("tool_output", "tool", tool_call_id=cid, text=text)
                continue

            # anything else (agent_reasoning/user_message/agent_message echoes,
            # world_state, inter_agent_communication, turn_aborted, review-mode
            # markers, unknown types) → escape hatch, no double-counting.
            if ts:
                yield RawEvent("codex", sid, ts, t or pt or "?", json.dumps(o))

        if sid:
            yield Session("codex", sid, cwd=meta.get("cwd"), cli_version=meta.get("ver"),
                          source=meta.get("src"), approval_policy=approval_policy,
                          sandbox_policy=sandbox_policy, first_ts=first_ts, last_ts=last_ts)

def default_providers():
    return [ClaudeProvider(), CodexProvider()]

# ---------------------------------------------------------------- ingest (the pass)
def ingest(conn, providers=None, capture=None):
    """Walk every provider's logs and upsert neutral Recs into conn.

    One transaction per file, rolled back on error, so a single malformed
    transcript can't corrupt the batch — same resilient-by-design stance as
    report.analyze(). Returns (files, recs) processed.
    """
    if providers is None: providers = default_providers()
    verbatim = (capture or CAPTURE) == "verbatim"
    n_files = n_recs = 0
    for prov in providers:
        for root in prov.roots():
            for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
                try:
                    for rec in prov.parse_file(f):
                        write(conn, rec, verbatim)
                        n_recs += 1
                    conn.commit()
                    n_files += 1
                except Exception:
                    conn.rollback()
                    continue
    return n_files, n_recs

# ---------------------------------------------------------------- retention (age-out)
def age_out(conn, days=None, now=None):
    """Null out raw_event.raw for rows older than a retention window (§6/§7).

    The write-side complement to the per-event RAW_CAP clamp: where RAW_CAP
    bounds a single row's size at ingest time, age_out() bounds the store's
    total growth over time. Structural columns (provider/session_id/ts/type/
    sha256) are untouched -- only the bulky verbatim JSON in `raw` is
    reclaimed, so the row (and its hash, for provenance) survives forever.

    `days` defaults to RAW_RETENTION_DAYS; `now` defaults to the real UTC
    clock but is an injectable seam so callers (tests) can pin it, same
    idiom as report.py's `today=` params. The `raw IS NOT NULL` guard makes
    a second run of the same (or wider) window a no-op, so the returned
    rowcount always reflects rows actually cleared by this call.
    """
    if days is None: days = RAW_RETENTION_DAYS
    if now is None: now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = (now - datetime.timedelta(days=days)).isoformat()
    cur = conn.execute("UPDATE raw_event SET raw=NULL WHERE raw IS NOT NULL AND ts < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


if __name__ == "__main__":
    os.makedirs(report.MACHINE_DIR, exist_ok=True)
    db = open_db()
    files, recs = ingest(db)
    cleared = age_out(db)
    row = lambda q: db.execute(q).fetchone()[0]
    print(f"canonical store: {DB_FILE}  (capture={CAPTURE})")
    print(f"  {files} files -> {recs} records")
    print(f"  sessions={row('SELECT count(*) FROM session')} "
          f"turns={row('SELECT count(*) FROM turn')} "
          f"tool_calls={row('SELECT count(*) FROM tool_call')} "
          f"segments={row('SELECT count(*) FROM segment')} "
          f"raw_events={row('SELECT count(*) FROM raw_event')}")
    print(f"  aged out raw payloads: {cleared} cleared (> {RAW_RETENTION_DAYS}d)")
