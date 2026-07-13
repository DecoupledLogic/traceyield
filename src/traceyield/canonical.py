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
    format (ClaudeProvider, CodexProvider -- see traceyield.providers),
  * write() *consumes* that stream and upserts it into the schema, blind to
    which provider produced it.
Adding a provider = adding one Provider class. Nothing else changes (see
traceyield.providers.base.Provider for the formal protocol, and
traceyield.providers.__init__'s docstring for how to register one).

Stdlib only (sqlite3). Runs alongside the existing pipeline (dual-write); it
never mutates report.py's stores. Privacy: TRACEYIELD_CAPTURE=verbatim stores
raw text; the default ("structural") stores only length + sha256.

E3-F2-S4: ClaudeProvider/CodexProvider (the producers) moved out of this file
into traceyield.providers.claude / traceyield.providers.codex, so this module
is left holding only the store/consumer role -- schema, open_db(), write(),
ingest(), age_out(), and the default provider registry. It imports the
provider classes from traceyield.providers (re-exported below, same class
objects, not copies) purely to preserve the pre-existing public names
(canonical.ClaudeProvider, canonical.CodexProvider, canonical.codex_tier,
canonical.default_providers) that the root compat shim, cli.py, and existing
tests depend on. That's a NEW dependency edge (canonical -> providers), but
not a re-introduction of the thing E3-F2 exists to prevent: providers is the
producer layer, one step above the neutral modules, not report.py, and
canonical.py still imports zero names from report.py.
"""
import os, glob, hashlib, sqlite3, datetime
from traceyield import classification, paths, pricing, transcripts
from traceyield.models import RawEvent, Segment, Session, ToolCall, Turn
from traceyield.providers import CODEX_TIER, ClaudeProvider, CodexProvider, codex_tier
# pricing/classification: kept imported here (not used by this module's own
# code anymore now that the producers moved out) purely so canonical.pricing/
# canonical.classification keep resolving to the shared modules for existing
# call sites/tests (tests/test_pricing.py's identity checks) -- a deliberate
# backward-compat re-export, not a real dependency of write()/ingest()/etc.
# models: the five neutral record dataclasses (Session/Turn/ToolCall/Segment/
# RawEvent) -- imported by name so canonical.Turn IS models.Turn (same class
# object), not a copy. transcripts: shared project_of()/result_text() plus
# (as of E3-F2-S4) iter_json_lines()/ms()/tool_kind(), re-exported below under
# their original canonical.py names for backward compatibility.
# paths.machine_id() replaces the last of these three. This module no longer
# imports report.py at all -- the reverse ingestion->reporting dependency is
# gone (E3-F2-S3; see docs/decisions/0008-installable-src-layout-package.md
# Phase 2, "providers depend only on neutral models/utilities").

# ---------------------------------------------------------------- config
# CAPTURE / RAW_RETENTION_DAYS / DB_FILE are centralized in traceyield.paths
# (E3-F2-S1) -- re-exported here so existing call sites/tests keep working.
CAPTURE = paths.CAPTURE   # "structural" | "verbatim"
RAW_CAP = 32 * 1024      # max bytes of raw_event.raw kept in verbatim mode (§7); not env-driven, stays here
RAW_RETENTION_DAYS = paths.RAW_RETENTION_DAYS   # age_out() window (§7)
SCHEMA_VERSION = 2   # v2: turn.project (per-turn project, see MIGRATIONS)
DB_FILE = paths.DB_FILE

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

# Session/Turn/ToolCall/Segment/RawEvent now live in traceyield.models
# (E3-F2-S3) -- imported by name above, so canonical.Session IS
# models.Session etc (same class object), not a redefinition here.

# ---------------------------------------------------------------- shared helpers
def sha(s):
    if s is None: return None
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()

def _b(x):
    return None if x is None else int(bool(x))

# _ms()/tool_kind()/TOOL_KIND (and the JSONL line reader, _iter_json_lines)
# moved to traceyield.transcripts in E3-F2-S4 -- both ClaudeProvider and
# CodexProvider need them (that's WHY they're "shared helpers"), and a
# provider module reaching back into canonical.py for them would be the same
# ingestion<-reporting-style layering mistake this feature exists to remove,
# just pointed at a different file. Re-exported here under their original
# names, same objects (not copies), for backward compatibility with existing
# call sites (write() below still uses sha()/_b(); _ms()/tool_kind() were
# only ever used by the providers, which now get them from transcripts
# directly) and tests (tests/test_canonical.py's TestHelpers).
_ms = transcripts.ms
_iter_json_lines = transcripts.iter_json_lines
tool_kind = transcripts.tool_kind
TOOL_KIND = transcripts.TOOL_KIND

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
            (rec.provider, rec.session_id, paths.machine_id(), rec.project, rec.cwd, rec.git_branch,
             rec.cli_version, rec.source, rec.approval_policy, rec.sandbox_policy,
             rec.first_ts, rec.last_ts))

# ---------------------------------------------------------------- producers (providers)
# ClaudeProvider / CodexProvider (plus Codex-only helpers codex_tier(),
# _codex_text(), _codex_tool_output(), CODEX_TIER) moved to
# traceyield.providers.claude / traceyield.providers.codex in E3-F2-S4 -- see
# that package for the parsing logic itself (unchanged). Imported and
# re-exported at the top of this module (same class/function objects, not
# copies) so canonical.ClaudeProvider / canonical.CodexProvider / canonical.codex_tier
# keep resolving for the root compat shim, cli.py, and existing tests.

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
    os.makedirs(paths.machine_dir(), exist_ok=True)
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
