#!/usr/bin/env python3
"""
Claude Code usage analytics — daily time-series edition.

Every transcript line is timestamped, so one run reconstructs the FULL history
bucketed by activity date. Aggregation into day / week / month views happens
client-side in report.html, so you can step through any period and see trends.

Each run:
  1. Ingests every transcript under CLAUDE_PROJECTS (and ~/.codex) into the
     canonical usage.db (canonical.py), then derives day/session metrics from
     it via aggregate() -- SQL GROUP BY over the provider-neutral turn/tool
     store is the source of truth. Falls back to the legacy direct-parse
     analyze() if the canonical path fails for any reason (never breaks a run).
     See docs/decisions/0001-aggregate-flip.md.
  2. Merges into daily_metrics.json (new data authoritative per date; older
     dates kept even if their transcripts get rotated away).
  3. Records today's model pricing in pricing_history.json.
  4. Regenerates report.html (self-contained interactive app; no dependencies).
  5. Checks PRICING against Anthropic's published rates and warns on drift
     (best-effort; never overwrites PRICING or fails the run).

Run daily:  python report.py       (wire to a scheduled task; see README note)
"""
import json, os, glob, html, datetime, re, socket, sys, urllib.request
from collections import defaultdict, Counter

# ---------------------------------------------------------------- config
HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_PROJECTS = os.path.expanduser(r"~/.claude/projects")

def machine_id():
    """Identity of the machine whose transcripts we're parsing.

    Each machine has its own ~/.claude/projects, so the artifacts derived from
    it (daily_metrics/session_metrics/report.html/run.log) are namespaced under
    machines/<machine_id>/ — otherwise one machine's run would clobber another's
    data in the shared repo. Defaults to the sanitized hostname; the
    TRACEYIELD_MACHINE env var overrides it (e.g. to make a machine write into a
    pre-existing directory whose name doesn't match its hostname)."""
    raw = (os.environ.get("TRACEYIELD_MACHINE") or "").strip() or socket.gethostname() or "unknown"
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-._")
    return slug or "unknown"

MACHINES_DIR = os.path.join(HERE, "machines")
MACHINE_DIR = os.path.join(MACHINES_DIR, machine_id())
DAILY_FILE = os.path.join(MACHINE_DIR, "daily_metrics.json")
SESSION_FILE = os.path.join(MACHINE_DIR, "session_metrics.json")
OUT_HTML = os.path.join(MACHINE_DIR, "report.html")
HEALTH_FILE = os.path.join(MACHINE_DIR, "health.json")
# Codex (OpenAI) CLI rollout logs — fingerprinted for schema drift even before a
# full parser exists, so the format is baselined from day one (see docs/).
CODEX_SESSIONS = os.path.expanduser(r"~/.codex/sessions")
# pricing_history is derived from the PRICING table (not from any machine's
# transcripts), so it's identical everywhere and stays shared at the repo root.
PRICING_FILE = os.path.join(HERE, "pricing_history.json")

# Base per-1M-token rates. Edit when Anthropic pricing changes.
# Cache multipliers fixed by the API: read=0.1x, write-5m=1.25x, write-1h=2x.
# Cost across all history is computed at THESE (current) rates for apples-to-
# apples comparison; the pricing trend chart shows how rates themselves moved.
# These are the authoritative source of truth (hand-verified against the
# Anthropic pricing page); check_pricing_drift() below re-verifies them each
# run and warns on mismatch — it never overwrites, since a bad scrape would
# retroactively distort every day's reported cost.
PRICING = {
    "opus":   (5.00, 25.00),
    "sonnet": (2.00, 10.00),   # Sonnet 5 intro pricing thru 2026-08-31 (std 3/15)
    "haiku":  (1.00,  5.00),
}
# Anthropic's published pricing page (Markdown). Anthropic exposes no pricing
# API — the Models API returns capabilities but no rates — so this doc page is
# the authoritative live source for the drift check.
PRICING_URL = "https://platform.claude.com/docs/en/docs/about-claude/pricing.md"
def cache_rates(inp): return dict(read=inp*0.10, w5m=inp*1.25, w1h=inp*2.0)
def tier(model):
    if not model: return None
    m = model.lower()
    if "opus" in m or "fable" in m: return "opus"
    if "sonnet" in m: return "sonnet"
    if "haiku" in m: return "haiku"
    return None

# ---------------------------------------------------------------- error taxonomy
ERROR_RULES = [
    ("read_before_write", ["file has not been read yet"], "Write/Edit before Read",
     "Read a file before editing it — the harness tracks read state and rejects edits to unread files."),
    ("stale_edit", ["file has been modified since read"], "Edit on stale file",
     "Re-Read the file right before editing when a formatter/other edit/external process may have changed it."),
    ("edit_no_match", ["string to replace not found","old_string","not unique","no replacement was performed"],
     "Edit string didn't match",
     "old_string must match byte-for-byte and be unique. Add surrounding context, or use replace_all."),
    ("shell_cmd_not_found", ["command not found","exit code 127"], "Command not found (shell mismatch)",
     "Windows: ls/python/etc aren't on the Bash PATH. Use the PowerShell tool for Windows commands."),
    ("shell_syntax", ["unexpected eof","syntax error near","eval: line","unexpected token"],
     "Shell quoting / path-escaping error",
     "Windows backslash paths break Bash quoting. Prefer the PowerShell tool, or forward slashes / single quotes."),
    ("user_rejected", ["user doesn't want to proceed","was rejected","permission for this action was denied","haven't granted"],
     "User rejected / permission denied",
     "Recurrent denials suggest an allowlist entry (settings.json) or a different approach for that action."),
    ("is_directory", ["eisdir","illegal operation on a directory","is a directory","directory does not exist"],
     "Treated a directory as a file", "Confirm the path is a file (Glob/LS) before Read/Write."),
    ("file_not_found", ["no such file","does not exist","cannot access","cannot find","not found"],
     "File / path not found", "Verify paths (Glob first); often a wrong relative path or an assumed file."),
    ("blocked_dangerous", ["remove-item on system path","on system path '/'"],
     "Blocked dangerous operation", "A destructive command hit a guard. Scope paths explicitly; avoid roots."),
    ("input_validation", ["inputvalidationerror"], "Tool input validation error",
     "Often a deferred/MCP tool called before its schema loaded via ToolSearch, or a bad parameter."),
    ("json_field", ["unknown json field"], "Unknown JSON field", "Stale/misspelled payload field — check current schema."),
    ("git_error", ["fatal:","exit code 128"], "Git error", "Bad ref / not a repo / cannot change dir — check repo state first."),
]
def classify(text):
    low = text.lower()
    for name, subs, _, _ in ERROR_RULES:
        if any(s in low for s in subs): return name
    return "other"
ERROR_META = {n: {"title": t, "fix": f} for n, _, t, f in ERROR_RULES}
ERROR_META["other"] = {"title": "Other / uncategorized", "fix": "Review examples; add a rule to ERROR_RULES if a pattern recurs."}

# ---------------------------------------------------------------- parse
def project_of(path, root=CLAUDE_PROJECTS):
    return os.path.relpath(path, root).split(os.sep)[0]
def result_text(b):
    c = b.get("content")
    if isinstance(c, str): return c
    if isinstance(c, list): return " ".join(x.get("text","") for x in c if isinstance(x, dict))
    return ""
def new_tool(): return {"calls":0,"out":0,"cost":0.0,"err":0}
def new_model(): return {"cost":0.0,"tok":{"input":0,"output":0,"cache_read":0,"cache_write_5m":0,"cache_write_1h":0}}
def new_day():
    return {"cost":0.0,
            "tok":{"input":0,"output":0,"cache_read":0,"cache_write_5m":0,"cache_write_1h":0},
            "msgs":0,"tool_results":0,"tool_errors":0,
            "sids":set(), "by_model":defaultdict(new_model),
            "by_project":defaultdict(lambda:{"cost":0.0,"msgs":0}),
            "by_tool":defaultdict(new_tool), "errors":Counter()}
def new_session():
    # sessions span days; accumulated globally (keyed by sessionId) so a single
    # runaway conversation is visible even when its cost is split across dates.
    return {"cost":0.0,
            "tok":{"input":0,"output":0,"cache_read":0,"cache_write_5m":0,"cache_write_1h":0},
            "msgs":0,"tool_results":0,"tool_errors":0,
            "project":None,"start":None,"end":None,"by_model":defaultdict(float)}

def analyze(root=CLAUDE_PROJECTS):
    """Parse Claude transcripts directly into (days, sessions).

    Retained as the equivalence oracle for aggregate() (see TestAggregateEquivalence
    in test_report.py) and as main()'s resilience fallback if the canonical-db path
    fails. It is NO LONGER the live production path — main() derives day/session
    metrics from aggregate() over usage.db instead (see docs/decisions/0001-
    aggregate-flip.md).

    Dedup: Claude Code replays the SAME assistant turn (same uuid) and its tool
    results into multiple transcript files on session resume/compaction. A
    billable turn is deduped by `uuid`; a tool_result is deduped by
    `tool_use_id`. Only replayed turns/results are counted once (the operator
    decided this is correct -- each turn is billed once); a line/block without
    that id is never deduped. `days`/`sessions` entries, and the day-active-
    session set / session start-end span, are only ever touched by a
    (non-duplicate) billable turn or a (non-duplicate) tool_result -- never by
    a plain prompt-only line -- so both are defined identically to aggregate()."""
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    days = defaultdict(new_day)
    sessions = defaultdict(new_session)
    # Run-scoped across ALL files: glob order matches canonical.ingest()'s
    # INSERT-OR-IGNORE-by-turn_id/call_id dedup, so the first occurrence
    # (across files) wins identically in both paths.
    seen_turn_ids = set()
    seen_result_ids = set()
    for f in files:
        proj = project_of(f, root)
        idname = {}
        try:
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line: continue
                try: o = json.loads(line)
                except: continue
                ts = o.get("timestamp")
                if not ts: continue
                d = ts[:10]
                m = o.get("message")
                if not isinstance(m, dict): continue
                sid = o.get("sessionId")
                content = m.get("content")
                content = content if isinstance(content, list) else []
                u = m.get("usage")

                if isinstance(u, dict):                        # a billable assistant turn
                    tid = o.get("uuid")
                    if tid is not None:
                        if tid in seen_turn_ids: continue       # replayed turn -- already billed
                        seen_turn_ids.add(tid)
                    D = days[d]; S = sessions[sid] if sid else None
                    if sid: D["sids"].add(sid)
                    if S is not None:
                        if S["project"] is None: S["project"] = proj
                        if S["start"] is None or ts < S["start"]: S["start"] = ts
                        if S["end"] is None or ts > S["end"]: S["end"] = ts
                    turn_tools = []
                    for b in content:
                        if not isinstance(b, dict): continue
                        if b.get("type") == "tool_use":
                            nm = b.get("name","?")
                            turn_tools.append(nm)
                            D["by_tool"][nm]["calls"] += 1
                            idname[b.get("id")] = nm
                    tr = tier(m.get("model"))
                    if tr is None: continue
                    inp=u.get("input_tokens",0) or 0; out=u.get("output_tokens",0) or 0
                    cr=u.get("cache_read_input_tokens",0) or 0; cc=u.get("cache_creation_input_tokens",0) or 0
                    det=u.get("cache_creation") or {}
                    w1h=det.get("ephemeral_1h_input_tokens",0) or 0; w5m=det.get("ephemeral_5m_input_tokens",0) or 0
                    if w1h+w5m==0 and cc>0: w5m=cc
                    ri,ro=PRICING[tr]; crate=cache_rates(ri)
                    cost=(inp*ri+out*ro+cr*crate["read"]+w5m*crate["w5m"]+w1h*crate["w1h"])/1e6
                    D["cost"]+=cost; D["msgs"]+=1
                    tk=D["tok"]; tk["input"]+=inp; tk["output"]+=out; tk["cache_read"]+=cr
                    tk["cache_write_5m"]+=w5m; tk["cache_write_1h"]+=w1h
                    bm=D["by_model"][tr]; bm["cost"]+=cost
                    bmt=bm["tok"]; bmt["input"]+=inp; bmt["output"]+=out; bmt["cache_read"]+=cr
                    bmt["cache_write_5m"]+=w5m; bmt["cache_write_1h"]+=w1h
                    bp=D["by_project"][proj]; bp["cost"]+=cost; bp["msgs"]+=1
                    if S is not None:
                        S["cost"]+=cost; S["msgs"]+=1
                        stk=S["tok"]; stk["input"]+=inp; stk["output"]+=out; stk["cache_read"]+=cr
                        stk["cache_write_5m"]+=w5m; stk["cache_write_1h"]+=w1h
                        S["by_model"][tr]+=cost
                    # tool calls serialize (~100% single-tool turns) → attribute the
                    # whole turn's cost+output to its one tool; else a pseudo-row.
                    tkey = turn_tools[0] if len(turn_tools)==1 else ("(final response)" if not turn_tools else "(multi-tool turn)")
                    bt=D["by_tool"][tkey]; bt["cost"]+=cost; bt["out"]+=out
                else:                                            # a user line: prompts and/or tool_results
                    for b in content:
                        if not isinstance(b, dict) or b.get("type") != "tool_result": continue
                        rid = b.get("tool_use_id")
                        if rid is not None:
                            if rid in seen_result_ids: continue  # replayed result -- already counted
                            seen_result_ids.add(rid)
                        D = days[d]; S = sessions[sid] if sid else None
                        if sid: D["sids"].add(sid)
                        if S is not None:
                            if S["project"] is None: S["project"] = proj
                            if S["start"] is None or ts < S["start"]: S["start"] = ts
                            if S["end"] is None or ts > S["end"]: S["end"] = ts
                        D["tool_results"] += 1
                        if S is not None: S["tool_results"] += 1
                        if b.get("is_error"):
                            D["tool_errors"] += 1
                            if S is not None: S["tool_errors"] += 1
                            D["errors"][classify(result_text(b))] += 1
                            enm = idname.get(rid)
                            if enm: D["by_tool"][enm]["err"] += 1
        except: continue
    # serialize (sets->counts kept as list length; round cost)
    out = {}
    for d, D in days.items():
        out[d] = {
            "cost": round(D["cost"], 4),
            "tok": D["tok"],
            "msgs": D["msgs"], "tool_results": D["tool_results"], "tool_errors": D["tool_errors"],
            "sessions": len(D["sids"]),
            "by_model": {m:{"cost":round(v["cost"],4),"tok":v["tok"]} for m,v in D["by_model"].items()},
            "by_project": {p:{"cost":round(v["cost"],4),"msgs":v["msgs"]} for p,v in D["by_project"].items()},
            "by_tool": {t:{"calls":v["calls"],"out":v["out"],"cost":round(v["cost"],4),"err":v["err"]} for t,v in D["by_tool"].items()},
            "errors": dict(D["errors"]),
        }
    sess = {}
    for sid, S in sessions.items():
        sess[sid] = {
            "cost": round(S["cost"], 4),
            "tok": S["tok"],
            "msgs": S["msgs"], "tool_results": S["tool_results"], "tool_errors": S["tool_errors"],
            "project": S["project"], "start": S["start"], "end": S["end"],
            "by_model": {m: round(c, 4) for m, c in S["by_model"].items()},
        }
    return out, sess

def aggregate(conn):
    """Derive (days, sessions) — the SAME shapes analyze() returns — via SQL
    GROUP BY aggregation over an open canonical-db connection (canonical.py's
    usage.db). This is the live production path (see main()); analyze() is kept
    as the equivalence oracle this is proven against (TestAggregateEquivalence
    in test_report.py) and as a resilience fallback.

    Equivalence rules honored (see docs/decisions/0001-aggregate-flip.md):
      - scoped to provider='claude' (Codex has no cost model yet);
      - cost/tok/msgs/by_model/by_project/session-cost only accumulate over
        turns with tier IS NOT NULL, mirroring analyze()'s
        `if tr is None: continue` gate. Cost is recomputed from tokens at the
        CURRENT PRICING/cache_rates() — never read from a stored column;
      - day `sessions`, `tool_results`, `tool_errors`, `errors`, and
        `by_tool[*].calls`/`.err` are NOT tier-filtered (they come from
        tool_call rows / the session-id union, independent of model tier);
      - `by_tool[*].calls` is bucketed by the linked TURN's day (join
        turn_id -> turn.ts), matching analyze()'s "a tool_use call belongs to
        its assistant turn's day". `by_tool[*].err`/`tool_results`/
        `tool_errors`/`errors` stay bucketed by tool_call.ts's own day (which
        tracks MAX(call, result) -- i.e. the RESULT's day), matching
        analyze()'s "a tool_result's error belongs to the result line's day".
        A call/result straddling UTC midnight is the reason these two differ;
      - `by_tool` cost/out attribution follows the turn's tool_use count via a
        turn_id -> tool_call join (0 -> "(final response)", 1 -> that tool's
        name, >1 -> "(multi-tool turn)");
      - session `start`/`end` are MIN/MAX(ts) over that session's turn AND
        tool_call rows -- NOT session.first_ts/last_ts (a canonical session
        row is keyed by the file's own span bookkeeping; deriving span from
        the turn+tool_call rows matches analyze() exactly turn-for-turn);
      - day `by_project` keys on each TURN's OWN `project` column (per-file,
        set at ingest) -- NOT the session's resolved `project` -- matching
        analyze()'s per-file cost attribution. A session's turns can span two
        project directories (e.g. a worktree switch mid-session); by_project
        must still split like analyze() does. `sessions[*].project` is the
        session-level, first-seen-wins value (from the session table) and is
        unrelated to this per-turn field.
    """
    days = defaultdict(new_day)
    sessions = defaultdict(new_session)

    # project per session (provider='claude'), for by_project / session.project
    project_of_sess = dict(conn.execute(
        "SELECT session_id, project FROM session WHERE provider='claude'"))

    # day `sessions` = distinct session_id over the UNION of turn + tool_call
    # rows on that day -- NOT tier-filtered (rule 3).
    for d, cnt in conn.execute("""
        SELECT day, COUNT(DISTINCT session_id) FROM (
          SELECT substr(ts,1,10) AS day, session_id FROM turn
            WHERE provider='claude' AND ts IS NOT NULL
          UNION
          SELECT substr(ts,1,10) AS day, session_id FROM tool_call
            WHERE provider='claude' AND ts IS NOT NULL
        ) GROUP BY day
    """):
        if d is not None:
            days[d]["sessions"] = cnt

    # tool_results / tool_errors / errors (day) and by_tool.err (day) -- NOT
    # tier-filtered (rule 3); grouped by tool_call.ts, which tracks MAX(call,
    # result) -- i.e. the RESULT's day once a result has arrived. This matches
    # analyze(), which counts a tool_result's error/tool_results/tool_errors
    # on the RESULT line's own day (a separate line from the call).
    for day, sid, name, ok, ec in conn.execute("""
        SELECT substr(ts,1,10), session_id, name, ok, error_class
        FROM tool_call WHERE provider='claude' AND ts IS NOT NULL
    """):
        if day is not None:
            D = days[day]
            if ok is not None:
                D["tool_results"] += 1
                if ok == 0:
                    D["tool_errors"] += 1
                    if ec: D["errors"][ec] += 1
            if name is not None and ok == 0:
                D["by_tool"][name]["err"] += 1

    # by_tool[*].calls -- bucketed by the CALL's linked TURN's day, not the
    # tool_call row's own ts day. tool_call.ts tracks MAX(call, result), so it
    # can drift to the result's day (or even the next day, e.g. an
    # AskUserQuestion answered after UTC midnight); analyze() always counts a
    # tool_use call under the assistant turn's own day. Join turn_id ->
    # turn.ts to match; every call's turn_id points at a kept turn, but fall
    # back to the tool_call's own ts day if that join is ever empty.
    for name, turn_day, tc_day in conn.execute("""
        SELECT tc.name, substr(t.ts,1,10), substr(tc.ts,1,10)
        FROM tool_call tc LEFT JOIN turn t
          ON t.turn_id = tc.turn_id AND t.provider = tc.provider
        WHERE tc.provider='claude' AND tc.name IS NOT NULL
    """):
        day = turn_day or tc_day
        if day is not None:
            days[day]["by_tool"][name]["calls"] += 1

    # session-level tool_results/tool_errors -- NOT tier-filtered (rule 7).
    for sid, ok in conn.execute("""
        SELECT session_id, ok FROM tool_call
        WHERE provider='claude' AND session_id IS NOT NULL
    """):
        if ok is not None:
            S = sessions[sid]
            S["tool_results"] += 1
            if ok == 0: S["tool_errors"] += 1

    # session span -- MIN/MAX(ts) over turn UNION tool_call rows (rule 7),
    # not session.first_ts/last_ts.
    for sid, lo, hi in conn.execute("""
        SELECT session_id, MIN(ts), MAX(ts) FROM (
          SELECT session_id, ts FROM turn WHERE provider='claude' AND ts IS NOT NULL
          UNION ALL
          SELECT session_id, ts FROM tool_call WHERE provider='claude' AND ts IS NOT NULL
        ) GROUP BY session_id
    """):
        S = sessions[sid]
        S["start"], S["end"] = lo, hi
        S["project"] = project_of_sess.get(sid)

    # tool_use name(s) per turn_id, for the by_tool cost/out attribution (rule 4).
    tool_names_by_turn = defaultdict(list)
    for turn_id, name in conn.execute("""
        SELECT turn_id, name FROM tool_call
        WHERE provider='claude' AND name IS NOT NULL
          AND turn_id IS NOT NULL AND turn_id != ''
    """):
        tool_names_by_turn[turn_id].append(name)

    # cost/tok/msgs/by_model/by_project/session-cost/by_tool(cost,out) --
    # tier-not-null turns only (rule 2). by_project keys on the TURN's OWN
    # project (per-file, populated at ingest by ClaudeProvider) -- NOT the
    # session's resolved project -- so a session whose turns span two project
    # directories still splits its cost exactly like analyze() (which
    # attributes each turn to the file it was parsed from). Falls back to the
    # session's project on the (should-never-happen-for-claude) chance a
    # turn's own project is NULL.
    for turn_id, sid, day, tr, inp, cr, w5m, w1h, out, tproj in conn.execute("""
        SELECT turn_id, session_id, substr(ts,1,10), tier,
               input_fresh, cache_read, cache_write_5m, cache_write_1h, output, project
        FROM turn WHERE provider='claude' AND tier IS NOT NULL AND ts IS NOT NULL
    """):
        ri, ro = PRICING[tr]; crate = cache_rates(ri)
        cost = (inp*ri + out*ro + cr*crate["read"] + w5m*crate["w5m"] + w1h*crate["w1h"]) / 1e6

        D = days[day]
        D["cost"] += cost; D["msgs"] += 1
        tk = D["tok"]; tk["input"] += inp; tk["output"] += out; tk["cache_read"] += cr
        tk["cache_write_5m"] += w5m; tk["cache_write_1h"] += w1h
        bm = D["by_model"][tr]; bm["cost"] += cost
        bmt = bm["tok"]; bmt["input"] += inp; bmt["output"] += out; bmt["cache_read"] += cr
        bmt["cache_write_5m"] += w5m; bmt["cache_write_1h"] += w1h
        proj = tproj or project_of_sess.get(sid)
        bp = D["by_project"][proj]; bp["cost"] += cost; bp["msgs"] += 1

        S = sessions[sid]
        S["cost"] += cost; S["msgs"] += 1
        stk = S["tok"]; stk["input"] += inp; stk["output"] += out; stk["cache_read"] += cr
        stk["cache_write_5m"] += w5m; stk["cache_write_1h"] += w1h
        S["by_model"][tr] += cost

        names = tool_names_by_turn.get(turn_id, [])
        tkey = names[0] if len(names) == 1 else ("(final response)" if not names else "(multi-tool turn)")
        bt = D["by_tool"][tkey]; bt["cost"] += cost; bt["out"] += out

    out = {}
    for d, D in days.items():
        out[d] = {
            "cost": round(D["cost"], 4),
            "tok": D["tok"],
            "msgs": D["msgs"], "tool_results": D["tool_results"], "tool_errors": D["tool_errors"],
            "sessions": D.get("sessions", 0),
            "by_model": {m: {"cost": round(v["cost"], 4), "tok": v["tok"]} for m, v in D["by_model"].items()},
            "by_project": {p: {"cost": round(v["cost"], 4), "msgs": v["msgs"]} for p, v in D["by_project"].items()},
            "by_tool": {t: {"calls": v["calls"], "out": v["out"], "cost": round(v["cost"], 4), "err": v["err"]}
                        for t, v in D["by_tool"].items()},
            "errors": dict(D["errors"]),
        }
    sess = {}
    for sid, S in sessions.items():
        sess[sid] = {
            "cost": round(S["cost"], 4),
            "tok": S["tok"],
            "msgs": S["msgs"], "tool_results": S["tool_results"], "tool_errors": S["tool_errors"],
            "project": S["project"], "start": S["start"], "end": S["end"],
            "by_model": {m: round(c, 4) for m, c in S["by_model"].items()},
        }
    return out, sess

# ---------------------------------------------------------------- persist
def merge_daily(newdays, path=DAILY_FILE):
    old = {}
    if os.path.exists(path):
        try: old = json.load(open(path, encoding="utf-8"))
        except: old = {}
    old.update(newdays)  # new authoritative per date; keeps rotated-out dates
    json.dump(old, open(path, "w", encoding="utf-8"), indent=0)
    return old
def merge_sessions(newsess, path=SESSION_FILE):
    # Same merge philosophy as days: a session lives in one transcript, so a
    # re-parse is fully authoritative for that id; sessions whose transcripts
    # have rotated away are preserved from the prior store.
    old = {}
    if os.path.exists(path):
        try: old = json.load(open(path, encoding="utf-8"))
        except: old = {}
    old.update(newsess)
    json.dump(old, open(path, "w", encoding="utf-8"), indent=0)
    return old
def record_pricing(path=PRICING_FILE):
    hist = {}
    if os.path.exists(path):
        try: hist = json.load(open(path, encoding="utf-8"))
        except: hist = {}
    hist[datetime.date.today().isoformat()] = {m:{"input":r[0],"output":r[1]} for m,r in PRICING.items()}
    json.dump(hist, open(path, "w", encoding="utf-8"), indent=2)
    return hist

# ------------------------------------------------------- pricing drift check
def _fetch_pricing_page(url=PRICING_URL, timeout=15):
    """Fetch the Anthropic pricing page as text. Raises on any network error."""
    req = urllib.request.Request(url, headers={"User-Agent": "traceyield-pricing-check"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def parse_pricing_page(md):
    """Scrape {tier: (input, output)} from the page's 'Model pricing' table.

    Columns are: Model | Base Input | 5m write | 1h write | cache hit | Output,
    so input is cell 1 and output is cell 5. Scoped to the Model pricing section
    only — the Batch and Fast-mode tables below it list the same tiers at other
    rates. For each tier we take the first non-deprecated/-retired row whose
    model name contains the tier keyword (the current flagship for that tier).
    """
    m = re.search(r"##\s*Model pricing(.*?)(?:\n##\s|\Z)", md, re.S)
    if not m: return {}
    out = {}
    for ln in m.group(1).splitlines():
        ln = ln.strip()
        if not ln.startswith("|"): continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < 6: continue
        name = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cells[0]).lower()  # drop md links
        if "deprecated" in name or "retired" in name: continue
        ip, op = re.search(r"\$([0-9.]+)", cells[1]), re.search(r"\$([0-9.]+)", cells[5])
        if not (ip and op): continue          # skips header / separator rows (no $)
        for t in ("opus", "sonnet", "haiku"):
            if t in name and t not in out:
                out[t] = (float(ip.group(1)), float(op.group(1)))
    return out

def check_pricing_drift(url=PRICING_URL):
    """Warn (to stdout) if PRICING has drifted from Anthropic's published rates.

    Best-effort and non-authoritative: it never raises and never mutates
    PRICING. On any failure (offline, page moved, layout changed) it prints a
    'skipped' note and returns []. Returns a list of human-readable drift lines.
    """
    try:
        published = parse_pricing_page(_fetch_pricing_page(url))
    except Exception as e:
        print(f"  pricing drift check skipped ({type(e).__name__}: {e})")
        return []
    if not published:
        print("  pricing drift check skipped (could not parse pricing page)")
        return []
    drift = []
    for tier, (ci, co) in PRICING.items():
        pub = published.get(tier)
        if pub is None:
            drift.append(f"{tier}: not found on pricing page")
        elif (ci, co) != pub:
            drift.append(f"{tier}: PRICING={ci}/{co} vs Anthropic={pub[0]}/{pub[1]} per 1M")
    if drift:
        print("  WARNING: PRICING drift vs Anthropic pricing page -- update PRICING in report.py:")
        for d in drift: print(f"      {d}")
    else:
        print(f"  pricing verified against Anthropic pricing page ({len(published)} tiers match)")
    return drift

# ------------------------------------------------- schema & coverage monitoring
#
# The parser above is resilient by design (`except: continue`), so a vendor
# schema change surfaces as SILENT under-counting, not a crash — a renamed usage
# field reads 0, a new model id maps to no tier and gets $0 attributed. These
# functions are the observability layer that catches that: each run fingerprints
# the SHAPE of the data actually seen and diffs it against a declared baseline,
# and reconciles the stored day-series against the dates transcripts still cover
# so drift and data holes get surfaced (stdout, health.json, the report) instead
# of quietly zeroing a day. Best-effort twins of check_pricing_drift(): they
# never raise and never change how parsing works.

# Declared baseline of the load-bearing shapes we understand today. Seeded from
# REAL data, not vendor docs — so already-normal fields (Claude's inference_geo/
# server_tool_use/iterations/speed/service_tier, the <synthetic> model id) don't
# false-alarm. A NEW value in one of these small, semantic categories is the
# earliest drift signal; when you confirm it's benign, add it here (that is the
# update ritual). We deliberately DON'T fingerprint the ~70 churny top-level
# telemetry keys Claude Code writes — only the handful the parser depends on,
# and only for disappearance (a rename that blinds the parser).
SCHEMA_EXPECT = {
    "claude": {
        "required_line_keys": {"timestamp", "sessionId", "message"},
        "usage_keys": {"input_tokens", "output_tokens", "cache_read_input_tokens",
                       "cache_creation_input_tokens", "cache_creation", "service_tier",
                       "inference_geo", "server_tool_use", "iterations", "speed"},
        "cache_creation_keys": {"ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"},
        "block_types": {"text", "thinking", "tool_use", "tool_result", "image"},
        "known_unmapped_models": {"<synthetic>"},   # real ids we intentionally skip
    },
    "codex": {
        "line_types": {"session_meta", "turn_context", "response_item", "event_msg",
                       "compacted", "inter_agent_communication", "world_state"},
        "payload_types": {"message", "user_message", "agent_message", "agent_reasoning",
                          "reasoning", "function_call", "function_call_output",
                          "token_count", "turn_aborted", "entered_review_mode",
                          "exited_review_mode"},
        "token_count_info_keys": {"total_token_usage", "last_token_usage",
                                  "model_context_window"},
    },
}
# Which fingerprint categories are diffed against the baseline for each provider,
# and (for Claude) which model ids count as "unmapped" (usage silently dropped).
DRIFT_CATS = {
    "claude": ["usage_keys", "cache_creation_keys", "block_types"],
    "codex":  ["line_types", "payload_types", "token_count_info_keys"],
}

def _fp(files, lines, jerr, seen, dates, flags, unknown_models):
    """Normalize a scan into a JSON-safe fingerprint (sets -> sorted lists)."""
    return {"files": files, "lines": lines, "json_errors": jerr,
            "seen": {k: sorted(v) for k, v in seen.items()},
            "dates": dict(dates), "flags": dict(flags),
            "unknown_models": sorted(unknown_models)}

def scan_claude(root=CLAUDE_PROJECTS):
    """One defensive pass over the Claude transcripts collecting only SHAPE and
    per-date coverage — never costs anything, so it stays cheap and can't skew
    metrics. Mirrors analyze()'s resilience (per-line/per-file try/except)."""
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    seen = {"line_keys": set(), "usage_keys": set(), "cache_creation_keys": set(),
            "block_types": set(), "models": set()}
    dates = defaultdict(int); flags = Counter(); unknown = set()
    nfiles = nlines = jerr = 0
    known_unmapped = SCHEMA_EXPECT["claude"]["known_unmapped_models"]
    for f in files:
        nfiles += 1
        try:
            for ln in open(f, encoding="utf-8"):
                ln = ln.strip()
                if not ln: continue
                nlines += 1
                try: o = json.loads(ln)
                except: jerr += 1; continue
                seen["line_keys"] |= set(o.keys())
                ts = o.get("timestamp")
                if ts: dates[ts[:10]] += 1
                m = o.get("message")
                if not isinstance(m, dict): continue
                c = m.get("content")
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type"):
                            seen["block_types"].add(b["type"])
                u = m.get("usage")
                if not isinstance(u, dict): continue
                seen["usage_keys"] |= set(u.keys())
                cc = u.get("cache_creation")
                if isinstance(cc, dict): seen["cache_creation_keys"] |= set(cc.keys())
                mdl = m.get("model")
                if mdl:
                    seen["models"].add(mdl)
                    if tier(mdl) is None and mdl not in known_unmapped:
                        unknown.add(mdl); flags["unmapped_model_turns"] += 1
        except: continue
    return _fp(nfiles, nlines, jerr, seen, dates, flags, unknown)

def scan_codex(root=CODEX_SESSIONS):
    """Fingerprint Codex rollout logs before a full parser exists — collects the
    type/payload/token-usage shapes (to baseline drift vs. the research doc) and
    flags sessions that have tool/message activity but no token_count events
    (they would cost $0 — the `codex exec` gotcha). Costs nothing; no parser."""
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    seen = {"line_types": set(), "payload_types": set(),
            "token_count_info_keys": set(), "models": set()}
    dates = defaultdict(int); flags = Counter(); unknown = set()
    nfiles = nlines = jerr = 0
    for f in files:
        nfiles += 1; had_usage = had_activity = False
        try:
            for ln in open(f, encoding="utf-8"):
                ln = ln.strip()
                if not ln: continue
                nlines += 1
                try: o = json.loads(ln)
                except: jerr += 1; continue
                ts = o.get("timestamp")
                if ts: dates[ts[:10]] += 1
                typ = o.get("type")
                if typ: seen["line_types"].add(typ)
                p = o.get("payload")
                if not isinstance(p, dict): continue
                pt = p.get("type")
                if pt: seen["payload_types"].add(pt)
                if typ == "turn_context" and p.get("model"):
                    mdl = p["model"]; seen["models"].add(mdl)
                    if "gpt-" not in mdl.lower(): unknown.add(mdl)
                if pt in ("function_call", "function_call_output", "message"):
                    had_activity = True
                if pt == "token_count":
                    had_usage = True
                    info = p.get("info")
                    if isinstance(info, dict):
                        seen["token_count_info_keys"] |= set(info.keys())
        except: continue
        if had_activity:
            flags["files_with_activity"] += 1
            if not had_usage: flags["files_without_usage"] += 1
    return _fp(nfiles, nlines, jerr, seen, dates, flags, unknown)

def schema_drift(fp, provider):
    """Human-readable drift lines: NEW values in load-bearing categories (a new
    field / model / message type to review), unmapped models (silently-dropped
    cost), and required top-level keys that never appeared (a likely rename that
    blinds the parser). Never raises; [] means no drift."""
    exp = SCHEMA_EXPECT.get(provider, {})
    out = []
    for cat in DRIFT_CATS.get(provider, []):
        known = exp.get(cat, set())
        for v in fp["seen"].get(cat, []):
            if v not in known:
                out.append(f"new {cat[:-1]}: {v!r} (not in SCHEMA_EXPECT baseline)")
    for mdl in fp.get("unknown_models", []):
        out.append(f"unmapped model: {mdl!r} (usage skipped, $0 attributed -- add it to tier())")
    if fp["lines"]:
        seen_lk = set(fp["seen"].get("line_keys", []))
        for rk in exp.get("required_line_keys", []):
            if rk not in seen_lk:
                out.append(f"required key not seen: {rk!r} (possible rename -- parser is blind to it)")
    return out

def coverage(days, scan_dates, today=None):
    """Reconcile the stored day-series against the dates transcripts still cover.
    Separates benign idle days from SUSPICIOUS holes: a date transcripts cover
    but the store never recorded, or a stored day with tool activity yet $0 cost
    (usage rows silently dropped). Also a freshness watermark (days since the
    store last advanced). Scoped from the first active day to `today`; ancient
    dates whose transcripts have rotated away aren't actionable, so we don't
    reconstruct them -- we only reconcile within the window we can still see."""
    today = today or datetime.date.today().isoformat()
    active = sorted(days)
    out = {"active_days": len(active), "first": active[0] if active else None,
           "last": active[-1] if active else None, "days_since_last_active": None,
           "calendar_gaps": [], "suspicious": [],
           "recoverable_window": None, "checked_through": today}
    if scan_dates:
        sd = sorted(scan_dates); out["recoverable_window"] = [sd[0], sd[-1]]
    if not active: return out
    one = datetime.timedelta(days=1)
    cur, end = datetime.date.fromisoformat(active[0]), datetime.date.fromisoformat(today)
    while cur <= end:
        ds = cur.isoformat()
        if ds not in days:
            out["calendar_gaps"].append(ds)
            if ds in scan_dates:   # transcripts have lines here but nothing recorded
                out["suspicious"].append({"date": ds,
                    "reason": "transcript lines exist for this date but no usage was recorded"})
        else:
            D = days[ds]
            if (D.get("tool_results", 0) or 0) > 0 and (D.get("cost", 0) or 0) == 0:
                out["suspicious"].append({"date": ds,
                    "reason": "tool activity but $0 cost (usage rows dropped -- likely model/schema drift)"})
        cur += one
    out["days_since_last_active"] = (end - datetime.date.fromisoformat(active[-1])).days
    return out

def build_health(days, claude_fp, codex_fp):
    """Assemble the per-run health record: schema drift + coverage for Claude,
    schema fingerprint for Codex. This is the machine-readable artifact behind
    the report's Data health panel and the run.log warnings."""
    return {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "machine": machine_id(),
        "providers": {
            "claude": {"scan": claude_fp, "drift": schema_drift(claude_fp, "claude"),
                       "coverage": coverage(days, claude_fp.get("dates", {}))},
            "codex": {"scan": codex_fp, "drift": schema_drift(codex_fp, "codex")},
        },
    }

def write_health(health, path=HEALTH_FILE):
    json.dump(health, open(path, "w", encoding="utf-8"), indent=2)
    return health

def _slim_health(h):
    """Trim the health record for embedding in the HTML payload: drop the big
    per-date map and the churny top-level key list, cap the gap list. The full
    record is what health.json keeps."""
    if not h: return None
    import copy
    s = copy.deepcopy(h)
    for P in s.get("providers", {}).values():
        sc = P.get("scan", {})
        sc.pop("dates", None)
        sc.get("seen", {}).pop("line_keys", None)
        g = P.get("coverage", {}).get("calendar_gaps")
        if g and len(g) > 60: P["coverage"]["calendar_gaps"] = g[-60:]
    return s

def print_health(health):
    """One-shot stdout summary (captured in run.log): schema drift per provider,
    data holes, and staleness. Best-effort — the report is already written."""
    for prov in ("claude", "codex"):
        P = health["providers"][prov]; sc = P["scan"]; drift = P.get("drift", [])
        if drift:
            print(f"  {prov} SCHEMA DRIFT ({len(drift)}) -- review & update SCHEMA_EXPECT/tier() in report.py:")
            for d in drift: print(f"      {d}")
        else:
            print(f"  {prov} schema OK ({sc['files']} files, {sc['lines']:,} lines, {sc['json_errors']} json errors)")
    xf = health["providers"]["codex"]["scan"].get("flags", {})
    if xf.get("files_without_usage"):
        print(f"      codex: {xf['files_without_usage']}/{xf.get('files_with_activity',0)} active sessions had NO token_count (would cost $0)")
    cov = health["providers"]["claude"].get("coverage", {})
    for s in cov.get("suspicious", []):
        print(f"  DATA HOLE {s['date']}: {s['reason']}")
    gaps = cov.get("calendar_gaps", [])
    if gaps:
        print(f"  {len(gaps)} calendar gap day(s) with no usage in [{cov.get('first')}..{cov.get('checked_through')}] (idle days are normal; see Data health panel)")
    dsl = cov.get("days_since_last_active")
    if dsl and dsl > 1:
        print(f"  WARNING: no recorded usage for {dsl} day(s) (last active {cov.get('last')})")

# ---------------------------------------------------------------- html
def top_sessions(sessions, n=50):
    """Highest-cost sessions, id attached, for the report payload (full set is
    persisted to session_metrics.json; only the top N are embedded in HTML)."""
    rows = [dict(id=sid, **s) for sid, s in sessions.items()]
    return sorted(rows, key=lambda s: s["cost"], reverse=True)[:n]

def build_html(days, sessions, pricing_hist, health=None):
    price_rows = []
    for mdl in ("opus","sonnet","haiku"):
        i,o = PRICING[mdl]; cr = cache_rates(i)
        price_rows.append(f"<tr><td>{mdl}</td><td class='num'>${i:.2f}</td><td class='num'>${o:.2f}</td>"
                          f"<td class='num'>${cr['read']:.2f}</td><td class='num'>${cr['w5m']:.2f}</td><td class='num'>${cr['w1h']:.2f}</td></tr>")
    payload = json.dumps({
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "sessions": top_sessions(sessions),
        "pricing": {m:{"input":r[0],"output":r[1]} for m,r in PRICING.items()},
        "meta": ERROR_META,
        "pricing_history": pricing_hist,
        "health": _slim_health(health),
    })
    tmpl = HTML_TMPL
    tmpl = tmpl.replace("__PAYLOAD__", payload)
    tmpl = tmpl.replace("__PRICEROWS__", "".join(price_rows))
    return tmpl

def metrics_via_canonical():
    """Live production path: ingest the canonical turn/tool/segment store
    (canonical.py) and derive (days, sessions) from it via aggregate() -- SQLite
    is the source of truth for the aggregates, not a parallel transcript walk.

    Imported lazily to avoid an import cycle (canonical imports report). Raises
    on any failure (ingest or aggregate) so main() can fall back to analyze();
    it never partially commits -- merge_daily/merge_sessions only run on the
    result this returns. See docs/canonical-data-model.md and
    docs/decisions/0001-aggregate-flip.md."""
    import canonical
    db = canonical.open_db()
    files, recs = canonical.ingest(db)
    print(f"Canonical store: {canonical.DB_FILE} (capture={canonical.CAPTURE}) "
          f"| {files} files -> {recs} records")
    try:
        newdays, newsess = aggregate(db)
    finally:
        db.close()
    return newdays, newsess

def main():
    os.makedirs(MACHINE_DIR, exist_ok=True)   # machines/<machine_id>/
    try:
        newdays, newsess = metrics_via_canonical()
    except Exception as e:
        print(f"Aggregate-from-db failed ({type(e).__name__}: {e}); fell back to analyze()")
        newdays, newsess = analyze()
    days = merge_daily(newdays)
    sessions = merge_sessions(newsess)
    pricing_hist = record_pricing()
    # Fingerprint both providers' data shapes and reconcile coverage, then embed
    # the health record in the report and persist it (best-effort; never fatal).
    health = build_health(days, scan_claude(), scan_codex())
    write_health(health)
    open(OUT_HTML, "w", encoding="utf-8").write(build_html(days, sessions, pricing_hist, health))
    tc = sum(d["cost"] for d in days.values()); tm = sum(d["msgs"] for d in days.values())
    te = sum(d["tool_errors"] for d in days.values()); tr = sum(d["tool_results"] for d in days.values())
    ds = sorted(days)
    span = f"({ds[0]}..{ds[-1]})" if ds else "(none)"
    print(f"Machine: {machine_id()} -> {MACHINE_DIR}")
    print(f"{len(days)} active days {span} | ${tc:,.2f} | {tm:,} turns | "
          f"{te}/{tr} tool errors ({te/max(tr,1)*100:.1f}%)")
    if sessions:
        top = max(sessions.values(), key=lambda s: s["cost"])
        print(f"{len(sessions):,} sessions | priciest ${top['cost']:,.2f} ({top.get('project','?')})")
    print(f"Report: {OUT_HTML}")
    print_health(health)    # schema drift + data holes (into run.log)
    check_pricing_drift()   # best-effort; report is already written above

# ---------------------------------------------------------------- template
HTML_TMPL = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TraceYield — Usage Report</title>
<style>
/* TraceYield design system — brand tokens (see design-system/tokens.css). Swap these to retheme. */
:root{--bg:#0B1220;--panel:#141B2B;--panel2:#1B2436;--ink:#E8ECF3;--mut:#94A0B4;--line:#263149;--accent:#12C99A;--accent-contrast:#04140F;
--grad:linear-gradient(120deg,#05B98A,#10B7D8 42%,#258CF8 70%,#7338FF);
--c1:#12C99A;--c2:#258CF8;--c3:#7338FF;--c4:#10B7D8;--c5:#F0A35E;--c6:#E5709B;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 90px}
h1{font-size:25px;margin:0 0 4px} h2{font-size:17px;margin:30px 0 10px;border-bottom:1px solid var(--line);padding-bottom:8px}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:4px}
.brand .mk{width:34px;height:34px;flex:none} .brand h1{margin:0}
.brand .wm{color:var(--accent)} .brand .ttl{color:var(--mut);font-weight:500;font-size:18px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;position:sticky;top:0;z-index:5}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.seg button{background:transparent;color:var(--mut);border:0;padding:7px 14px;font-size:13px;cursor:pointer}
.seg button.on{background:var(--accent);color:var(--accent-contrast);font-weight:600}
select{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px}
.stepper{display:inline-flex;align-items:center;gap:8px;margin-left:auto}
.stepper button{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;width:34px;height:34px;font-size:16px;cursor:pointer}
.stepper button:disabled{opacity:.35;cursor:default} .pname{font-weight:600;min-width:150px;text-align:center}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px}
.klabel{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.kval{font-size:24px;font-weight:650;margin-top:5px} .delta{font-size:12px;margin-top:5px}
.delta.up{color:#F0A35E} .delta.down{color:#34D399} .delta.flat{color:var(--mut)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-top:12px;overflow-x:auto}
.chart{width:100%;height:auto} .grid{stroke:var(--line);stroke-width:1}
.ytick{fill:var(--mut);font-size:10px;text-anchor:end} .xtick{fill:var(--mut);font-size:10px;text-anchor:middle}
.dot{cursor:pointer} .legend{margin-top:8px;display:flex;flex-wrap:wrap;gap:14px}
.leg{font-size:12px;color:var(--mut);display:flex;align-items:center;gap:6px} .leg i{width:10px;height:10px;border-radius:2px;display:inline-block}
.hbars{display:flex;flex-direction:column;gap:7px}
.hbar{display:grid;grid-template-columns:160px 1fr 78px;align-items:center;gap:10px;font-size:13px}
.hlabel{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.htrack{background:var(--panel2);border-radius:5px;height:15px;overflow:hidden}
.hfill{display:block;height:100%;border-radius:5px} .hval{color:var(--mut);text-align:right;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.muted{color:var(--mut)} .mono{font-family:ui-monospace,Consolas,monospace;font-size:11px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px} @media(max-width:680px){.two{grid-template-columns:1fr}.hbar{grid-template-columns:110px 1fr 64px}}
.foot{color:var(--mut);font-size:12px;margin-top:36px;border-top:1px solid var(--line);padding-top:16px}
.hint{color:var(--mut);font-size:12px}
input[type=number]{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 6px;width:56px;font-size:12px}
.doc h3{font-size:15px;margin:18px 0 8px;color:var(--ink)} .doc h3:first-child{margin-top:0}
.doc dl{margin:0} .doc dt{font-weight:600;margin-top:11px} .doc dd{margin:2px 0 0;color:var(--mut)}
.doc dt .mono{color:var(--accent);margin-left:4px} .doc ul{margin:6px 0 0;padding-left:20px} .doc li{margin:7px 0}
.doc p{margin:8px 0}
#health{margin-top:14px} .hhdr{margin-bottom:4px;font-size:15px}
.hpill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-right:8px;vertical-align:middle}
.hpill.ok{background:#062A20;color:#6EE7B7} .hpill.warn{background:#2E1D0A;color:#FBBF6E}
.hrow{font-size:13px;padding:8px 0;border-top:1px solid var(--line)} .hrow:first-of-type{border-top:0}
.hrow.warn{color:#FBBF6E} .hrow.ok{color:var(--mut)} .hrow b{color:var(--ink)}
.hrow ul{margin:6px 0 0;padding-left:20px} .hrow li{margin:3px 0}
</style></head><body><div class="wrap">
<div class="brand"><span class="mk"><svg viewBox="0 0 1254 1254" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="tym" x1="300" y1="280" x2="900" y2="985" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#05b98a"/><stop offset="0.46" stop-color="#10b7d8"/><stop offset="0.72" stop-color="#258cf8"/><stop offset="1" stop-color="#7338ff"/></linearGradient></defs><path d="M627 228 L952 424 L952 806 L627 1002 L301 806 L301 424 Z" fill="none" stroke="url(#tym)" stroke-width="70" stroke-linecap="round" stroke-linejoin="round"/><g fill="url(#tym)"><rect x="432" y="603" width="77" height="165" rx="38.5"/><rect x="587" y="452" width="77" height="316" rx="38.5"/><rect x="741" y="571" width="77" height="197" rx="38.5"/></g></svg></span>
<h1>Trace<span class="wm">Yield</span> <span class="ttl">— Claude Code Usage &amp; Health</span></h1></div>
<div class="sub" id="sub"></div>

<div class="controls">
  <div class="seg" id="gran">
    <button data-g="day" class="on">Day</button><button data-g="week">Week</button><button data-g="month">Month</button>
  </div>
  <label class="hint">Trend metric&nbsp;
    <select id="metric">
      <option value="cost">Cost ($)</option>
      <option value="tokens">Total tokens</option>
      <option value="turns">Assistant turns</option>
      <option value="errrate">Tool error rate (%)</option>
      <option value="errors">Tool errors (count)</option>
      <option value="sessions">Sessions</option>
    </select>
  </label>
  <div class="stepper">
    <button id="prev" title="Previous (←)">‹</button>
    <span class="pname" id="pname"></span>
    <button id="next" title="Next (→)">›</button>
  </div>
</div>

<div class="cards" id="cards"></div>

<h2>Trend <span class="hint" id="trendhint"></span></h2>
<div class="panel" id="trend"></div>

<h2>Selected period — breakdown</h2>
<div class="two">
  <div class="panel"><div class="muted" style="margin-bottom:10px">Cost by project</div><div id="byproj"></div></div>
  <div class="panel"><div class="muted" style="margin-bottom:10px">Cost by model tier</div><div id="bymodel"></div></div>
</div>
<div class="panel" id="routepanel">
  <div class="muted" style="margin-bottom:6px">Model-routing savings estimate <span class="hint" id="routehint"></span></div>
  <div class="hint" style="margin-bottom:12px">Recomputes this period&rsquo;s <b>Opus</b> token usage at a cheaper tier&rsquo;s rates &mdash; the savings from routing routine work with <span class="mono">/model</span>. Assume <input id="routeshare" type="number" value="30" min="0" max="100" step="5">% of Opus is safely routable to <select id="routetier"><option value="sonnet">Sonnet</option><option value="haiku">Haiku</option></select>. Upper bound &mdash; keep quality-sensitive work on Opus.</div>
  <div id="routeout"></div>
</div>
<div class="panel"><div class="muted" style="margin-bottom:10px">Token composition</div><div id="comp"></div></div>
<div class="panel"><div class="muted" style="margin-bottom:10px">Tool usage (calls)</div><div id="tools"></div></div>

<h2>Tokens &amp; cost per tool <span class="hint" id="waste"></span></h2>
<div class="panel">
<div class="hint" style="margin-bottom:8px">Full turn cost attributed to its single tool — Claude Code serializes tool calls, so this is <b>exact</b> for tool turns (input/cache can't be split further until a custom harness logs per-tool usage). <b>Est. waste</b> = errors &times; avg cost/call &times; retry factor <input id="retry" type="number" value="1" min="0" step="0.5"> (assumed extra turns per error).</div>
<table><thead><tr><th>Tool</th><th class="num">Calls</th><th class="num">Output tok</th><th class="num">Cost</th><th class="num">Errors</th><th class="num">Err rate</th><th class="num">Est. waste</th></tr></thead><tbody id="tooltbody"></tbody></table>
</div>

<h2>Selected period — errors &amp; fixes</h2>
<div class="panel"><table><thead><tr><th>Pattern</th><th class="num">Count</th><th class="num">Share</th><th>Suggested fix</th></tr></thead><tbody id="errtbody"></tbody></table></div>

<h2>Top sessions by cost <span class="hint">&mdash; all time &middot; catch runaway conversations</span></h2>
<div class="panel">
<table><thead><tr><th>Session</th><th>Project</th><th>Span</th><th class="num">Turns</th><th class="num">Tokens</th><th class="num">Cost</th><th class="num">Err rate</th><th>Tier mix ($)</th></tr></thead>
<tbody id="sesstbody"></tbody></table>
<div class="muted" style="margin-top:8px">A session is one conversation (distinct sessionId), summed across every day it touched. Top 50 by cost. A single session far above the rest usually means a long, uncleared context re-read every turn &mdash; <span class="mono">/clear</span> between tasks.</div>
</div>

<h2>Model pricing (tracked daily)</h2>
<div class="panel"><div class="muted" style="margin-bottom:6px">Input price $/1M over time</div><div id="pricechart"></div></div>
<div class="panel"><table>
<thead><tr><th>Model tier</th><th class="num">Input</th><th class="num">Output</th><th class="num">Cache read</th><th class="num">Write 5m</th><th class="num">Write 1h</th></tr></thead>
<tbody>__PRICEROWS__</tbody></table>
<div class="muted" style="margin-top:8px">Per 1M tokens. Cache read = 0.1&times; input, write-5m = 1.25&times;, write-1h = 2&times;. All-history cost is computed at current rates for comparability; this chart shows how the rates themselves moved. Edit <span class="mono">PRICING</span> in report.py when Anthropic changes prices.</div></div>

<h2>How to read this report</h2>
<div class="panel doc">
<h3>Where the money goes — the five token line-items</h3>
<p>Each request re-sends the whole conversation so far. To avoid re-billing all of it at full price, the API <b>caches</b> a stable prefix of the prompt. Your tokens split into five lines, each a multiple of the model's base <b>input</b> price:</p>
<dl>
<dt>Fresh input <span class="mono">1&times;</span></dt><dd>Brand-new tokens the model reads for the first time (a newly opened file, your latest message). Full input price.</dd>
<dt>Cache write &mdash; 5&nbsp;min <span class="mono">1.25&times;</span></dt><dd>Storing a chunk in the cache the first time costs a 25% premium. It stays reusable for 5 minutes.</dd>
<dt>Cache write &mdash; 1&nbsp;hour <span class="mono">2&times;</span></dt><dd>Same, kept for an hour &mdash; double the input price to write. Claude Code uses 1h caching, which is why this line is large.</dd>
<dt>Cache read <span class="mono">0.1&times;</span></dt><dd>Reading tokens already in the cache costs a tenth of input price. This is the payoff of caching &mdash; but because your ~154K-token context is re-read on <em>every</em> turn, it&rsquo;s still your single biggest cost line by volume.</dd>
<dt>Output <span class="mono">output rate</span></dt><dd>Tokens the model generates (its reply + tool calls), billed at the separate, higher output price (Opus $25/1M).</dd>
</dl>
<p class="muted">A cached token you reuse costs ~8% of writing it fresh at 1h TTL (0.1 vs 1.25), so caching pays off after ~2 reuses. The risk is <b>invalidation</b>: editing a file or changing tools near the front of the prompt forces an expensive re-write of everything after it.</p>
<h3>How to use it to improve</h3>
<ul>
<li><b>Right-size the model.</b> ~98% of spend is Opus. Route routine work (reading, simple edits, exploration) to Sonnet with <span class="mono">/model</span> &mdash; cheaper input and 0.1&times; cache reads at $0.20 vs $0.50.</li>
<li><b>Keep context small.</b> Cost &asymp; context size &times; turns; the ~154K/turn re-read is the engine. <span class="mono">/clear</span> between tasks so each turn re-reads less.</li>
<li><b>Cut errors = cut wasted turns.</b> Each tool error &asymp; one extra turn that re-reads the full context. The per-tool panel&rsquo;s <b>Est. waste</b> column puts a dollar figure on it. Top offenders: Windows shell errors (use PowerShell for Windows commands) and Write/Edit-before-Read (Read first).</li>
<li><b>Watch the trend, not the day.</b> Switch to Week/Month and step with &larr;/&rarr; to see whether cost-per-turn and error rate improve after a change.</li>
</ul>
<h3>Glossary</h3>
<dl>
<dt>Turn</dt><dd>One assistant response (usually one tool call). &ldquo;Assistant turns&rdquo; counts these.</dd>
<dt>Session</dt><dd>One Claude Code conversation (a distinct sessionId).</dd>
<dt>Tool error rate</dt><dd>Share of tool results returned as errors. Lower is better.</dd>
<dt>Est. waste</dt><dd>Modeled error cost = errors &times; avg cost/call &times; retry factor. Cost/call is exact; the retry factor (how many extra turns an error triggers) is your tunable assumption.</dd>
<dt>vs prev</dt><dd>Change from the previous period at the current granularity.</dd>
<dt>Top sessions</dt><dd>Highest-cost individual conversations across all history. A single session far above the rest is a runaway context &mdash; the cheapest thing to fix.</dd>
<dt>Routing estimate</dt><dd>This period&rsquo;s Opus tokens recosted at Sonnet/Haiku rates, scaled by your routable-share assumption. An upper bound on <span class="mono">/model</span> savings, not a promise.</dd>
</dl>
</div>

<h2>Data health</h2>
<div class="panel" id="health"></div>

<div class="foot">Rerun daily: <span class="mono">python report.py</span>. Data bucketed by activity timestamp &rarr; <span class="mono">daily_metrics.json</span>; prices &rarr; <span class="mono">pricing_history.json</span>. Cost computed at current pricing. Use ← / → to step periods.</div>
</div>

<script>
const DATA = __PAYLOAD__;
const META = DATA.meta;
const $ = s => document.querySelector(s);
const fmtUSD = v => "$"+(v>=1000? v.toLocaleString(undefined,{maximumFractionDigits:0}) : v.toFixed(2));
const fmtTok = v => v>=1e9?(v/1e9).toFixed(2)+"B":v>=1e6?(v/1e6).toFixed(1)+"M":v>=1e3?(v/1e3).toFixed(0)+"K":Math.round(v);
const fmtInt = v => Math.round(v).toLocaleString();
const esc = s => String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
// Claude Code encodes a project's absolute path as its dir name (path
// separators → "-"), so every project on a machine shares a long, machine-
// specific root (e.g. "C--Users-charl-source-repos-"). Compute the common
// leading path SEGMENTS across all projects and strip them, so labels read as
// just the repo name on ANY machine (no hardcoded prefix). Segment-wise (split
// on "-") avoids cutting a name mid-word and handles the bare-root project.
const PROJ_STRIP = (function(){
  const lists=[];
  const push=p=>{if(p)lists.push(p.split("-"));};
  for(const dk in DATA.days){const bp=DATA.days[dk].by_project||{};for(const p in bp)push(p);}
  (DATA.sessions||[]).forEach(s=>push(s.project));
  if(lists.length<2)return 0;               // can't infer a shared root from one project
  const n=Math.min(...lists.map(l=>l.length));
  let common=0;
  for(let i=0;i<n;i++){
    const seg=lists[0][i];
    if(lists.every(l=>l[i]===seg))common=i+1; else break;
  }
  return common;
})();
const clean = p => p ? (p.split("-").slice(PROJ_STRIP).join("-")||"(root)") : "(root)";
const MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

const dayKeys = Object.keys(DATA.days).sort();
function mondayOf(ds){const d=new Date(ds+"T00:00:00Z");let wd=(d.getUTCDay()+6)%7;d.setUTCDate(d.getUTCDate()-wd);return d.toISOString().slice(0,10);}
function tokTotal(t){return t.input+t.output+t.cache_read+t.cache_write_5m+t.cache_write_1h;}

function blankPeriod(key,label){return{key,label,cost:0,
  tok:{input:0,output:0,cache_read:0,cache_write_5m:0,cache_write_1h:0},
  msgs:0,tool_results:0,tool_errors:0,sessions:0,
  by_model:{},by_project:{},by_tool:{},errors:{},dates:[]};}
function addNum(dst,src,keys){keys.forEach(k=>dst[k]+=src[k]||0);}
function addCounter(dst,src){for(const k in src)dst[k]=(dst[k]||0)+src[k];}
function addModel(dst,src){for(const t in src){dst[t]=dst[t]||{cost:0,tok:{input:0,output:0,cache_read:0,cache_write_5m:0,cache_write_1h:0}};dst[t].cost+=src[t].cost||0;const st=src[t].tok||{};for(const k in dst[t].tok)dst[t].tok[k]+=st[k]||0;}}
function addProj(dst,src){for(const p in src){dst[p]=dst[p]||{cost:0,msgs:0};for(const k in src[p])dst[p][k]=(dst[p][k]||0)+src[p][k];}}
function addTool(dst,src){for(const t in src){dst[t]=dst[t]||{calls:0,out:0,cost:0,err:0};for(const k in src[t])dst[t][k]=(dst[t][k]||0)+src[t][k];}}

function aggregate(gran){
  const groups=new Map();
  for(const dk of dayKeys){
    let key,label;
    if(gran==="day"){key=dk;label=dk;}
    else if(gran==="week"){key=mondayOf(dk);label="wk of "+key;}
    else{key=dk.slice(0,7);const[y,m]=key.split("-");label=MONTHS[+m-1]+" "+y;}
    if(!groups.has(key))groups.set(key,blankPeriod(key,label));
    const P=groups.get(key),D=DATA.days[dk];
    P.cost+=D.cost; P.msgs+=D.msgs; P.tool_results+=D.tool_results; P.tool_errors+=D.tool_errors;
    P.sessions+=D.sessions; P.dates.push(dk);
    addNum(P.tok,D.tok,Object.keys(P.tok));
    addModel(P.by_model,D.by_model); addProj(P.by_project,D.by_project);
    addTool(P.by_tool,D.by_tool); addCounter(P.errors,D.errors);
  }
  return [...groups.values()].sort((a,b)=>a.key<b.key?-1:1);
}

const METRICS={
  cost:{f:p=>p.cost,fmt:fmtUSD,label:"Cost"},
  tokens:{f:p=>tokTotal(p.tok),fmt:fmtTok,label:"Total tokens"},
  turns:{f:p=>p.msgs,fmt:fmtInt,label:"Assistant turns"},
  errrate:{f:p=>p.tool_results?p.tool_errors/p.tool_results*100:0,fmt:v=>v.toFixed(1)+"%",label:"Tool error rate"},
  errors:{f:p=>p.tool_errors,fmt:fmtInt,label:"Tool errors"},
  sessions:{f:p=>p.sessions,fmt:fmtInt,label:"Sessions"},
};

let state={gran:"day",metric:"cost",idx:0,periods:[]};

function svgLine(xs,vals,fmt,sel,color){
  const w=880,h=230,pad=46,n=xs.length;
  if(!n)return"<p class='muted'>No data.</p>";
  const mx=Math.max(...vals,0)*1.1||1;
  const X=i=>pad+i*(w-2*pad)/Math.max(n-1,1), Y=v=>h-pad-(v/mx)*(h-2*pad);
  let s=`<svg viewBox='0 0 ${w} ${h}' class='chart'>`;
  for(let g=0;g<5;g++){const gy=pad+g*(h-2*pad)/4,val=mx*(1-g/4);
    s+=`<line x1='${pad}' y1='${gy}' x2='${w-pad}' y2='${gy}' class='grid'/><text x='${pad-6}' y='${gy+3}' class='ytick'>${fmt(val)}</text>`;}
  s+=`<polyline points='${vals.map((v,i)=>X(i)+","+Y(v)).join(" ")}' fill='none' stroke='${color}' stroke-width='2'/>`;
  vals.forEach((v,i)=>{const r=i===sel?5:2.6,c=i===sel?"#fff":color;
    s+=`<circle class='dot' cx='${X(i)}' cy='${Y(v)}' r='${r}' fill='${c}' stroke='${color}' stroke-width='${i===sel?2:0}' onclick='pick(${i})'><title>${esc(xs[i])}: ${fmt(v)}</title></circle>`;});
  const step=Math.max(1,Math.ceil(n/10));
  for(let i=0;i<n;i+=step)s+=`<text x='${X(i)}' y='${h-pad+16}' class='xtick'>${esc(xs[i].length>7?xs[i].slice(5):xs[i])}</text>`;
  return s+"</svg>";
}
function hbars(rows,fmt,color){
  if(!rows.length)return"<p class='muted'>None.</p>";
  const mx=Math.max(...rows.map(r=>r[1]))||1;
  return"<div class='hbars'>"+rows.map(([l,v])=>
    `<div class='hbar'><span class='hlabel'>${esc(l)}</span><span class='htrack'><span class='hfill' style='width:${v/mx*100}%;background:${color}'></span></span><span class='hval'>${fmt(v)}</span></div>`).join("")+"</div>";
}

function render(){
  const P=state.periods, i=state.idx, cur=P[i], prev=P[i-1];
  const M=METRICS[state.metric];
  // stepper
  $("#pname").textContent=cur.label; $("#prev").disabled=i<=0; $("#next").disabled=i>=P.length-1;
  const range=cur.dates.length>1?` · ${cur.dates[0]} → ${cur.dates[cur.dates.length-1]}`:"";
  // KPI cards
  const kd=(label,val,d,dfmt)=>{let dh="";if(d!==null){const c=d>0?"up":d<0?"down":"flat",sg=d>0?"+":"";dh=`<div class='delta ${c}'>${sg}${dfmt(d)} vs prev</div>`;}
    return `<div class='card'><div class='klabel'>${label}</div><div class='kval'>${val}</div>${dh}</div>`;};
  const errRate=cur.tool_results?cur.tool_errors/cur.tool_results*100:0;
  const prevErr=prev&&prev.tool_results?prev.tool_errors/prev.tool_results*100:null;
  $("#cards").innerHTML=[
    kd("Cost",fmtUSD(cur.cost),prev?cur.cost-prev.cost:null,v=>fmtUSD(Math.abs(v))),
    kd("Total tokens",fmtTok(tokTotal(cur.tok)),prev?tokTotal(cur.tok)-tokTotal(prev.tok):null,v=>fmtTok(Math.abs(v))),
    kd("Assistant turns",fmtInt(cur.msgs),prev?cur.msgs-prev.msgs:null,v=>fmtInt(Math.abs(v))),
    kd("Sessions",fmtInt(cur.sessions),prev?cur.sessions-prev.sessions:null,v=>fmtInt(Math.abs(v))),
    kd("Tool error rate",errRate.toFixed(1)+"%",prevErr!==null?errRate-prevErr:null,v=>Math.abs(v).toFixed(1)+"pp"),
  ].join("");
  $("#sub").innerHTML=`Generated ${esc(DATA.generated)} · ${dayKeys.length} active days (${dayKeys[0]} → ${dayKeys[dayKeys.length-1]}) · viewing <b>${cur.label}</b>${range}`;
  // trend
  $("#trendhint").textContent="— "+M.label+" by "+state.gran+" · click a point or use ← →";
  $("#trend").innerHTML=svgLine(P.map(p=>p.label),P.map(M.f),M.fmt,i,"#12C99A");
  // breakdowns
  $("#byproj").innerHTML=hbars(Object.entries(cur.by_project).map(([p,d])=>[clean(p),d.cost]).sort((a,b)=>b[1]-a[1]),v=>fmtUSD(v),"#258CF8");
  $("#bymodel").innerHTML=hbars(Object.entries(cur.by_model).map(([m,d])=>[m,d.cost]).sort((a,b)=>b[1]-a[1]),v=>fmtUSD(v),"#7338FF");
  const t=cur.tok;
  $("#comp").innerHTML=hbars([["Cache read",t.cache_read],["Cache write 1h",t.cache_write_1h],["Cache write 5m",t.cache_write_5m],["Output",t.output],["Fresh input",t.input]],fmtTok,"#10B7D8");
  $("#tools").innerHTML=hbars(Object.entries(cur.by_tool).filter(([t,v])=>v.calls>0).map(([t,v])=>[t,v.calls]).sort((a,b)=>b[1]-a[1]).slice(0,15),fmtInt,"#12C99A");
  // tokens & cost per tool
  const rf=parseFloat($("#retry").value); const rfac=isNaN(rf)?1:rf;
  const trows=Object.entries(cur.by_tool).sort((a,b)=>b[1].cost-a[1].cost);
  let waste=0;
  $("#tooltbody").innerHTML=trows.map(([t,v])=>{
    const per=v.calls?v.cost/v.calls:0, w=v.err*per*rfac; waste+=w;
    const er=v.calls?v.err/v.calls*100:0;
    return `<tr><td>${esc(t)}</td><td class='num'>${v.calls?fmtInt(v.calls):"—"}</td><td class='num'>${fmtTok(v.out)}</td><td class='num'>${fmtUSD(v.cost)}</td><td class='num'>${v.err||""}</td><td class='num'>${v.calls&&v.err?er.toFixed(1)+"%":""}</td><td class='num'>${w?fmtUSD(w):""}</td></tr>`;
  }).join("");
  $("#waste").textContent=waste?`— ≈ ${fmtUSD(waste)} wasted on errors this period (retry ×${rfac})`:"";
  // errors
  const te=Object.values(cur.errors).reduce((a,b)=>a+b,0)||1;
  $("#errtbody").innerHTML=Object.entries(cur.errors).sort((a,b)=>b[1]-a[1]).map(([k,v])=>{
    const m=META[k]||{title:k,fix:""};
    return `<tr><td><b>${esc(m.title)}</b><br><span class='muted mono'>${esc(k)}</span></td><td class='num'>${v}</td><td class='num'>${Math.round(v/te*100)}%</td><td>${esc(m.fix)}</td></tr>`;
  }).join("")||"<tr><td colspan=4 class='muted'>No tool errors in this period. 🎉</td></tr>";
  renderRoute(cur);
}
window.pick=i=>{state.idx=i;render();};

// ---- model-routing savings estimate (selected period) ----
function costAtRates(tok,r){return (tok.input*r.input + tok.output*r.output +
  tok.cache_read*r.input*0.1 + tok.cache_write_5m*r.input*1.25 + tok.cache_write_1h*r.input*2.0)/1e6;}
function renderRoute(cur){
  const el=$("#routeout"), hint=$("#routehint");
  const opus=cur.by_model&&cur.by_model.opus;
  if(!opus||!opus.cost||!opus.tok){el.innerHTML="<p class='muted'>No Opus usage in this period.</p>";hint.textContent="";return;}
  let share=parseFloat($("#routeshare").value); share=isNaN(share)?0:Math.max(0,Math.min(100,share))/100;
  const tier=$("#routetier").value, rates=DATA.pricing[tier];
  const atTier=costAtRates(opus.tok,rates);
  const fullDelta=opus.cost-atTier;          // savings if 100% of Opus moved
  const sav=share*fullDelta;                 // savings at the chosen routable share
  const pct=opus.cost?fullDelta/opus.cost*100:0;
  const newTotal=cur.cost-sav;
  const card=(l,v,s,cls)=>`<div class='card'><div class='klabel'>${l}</div><div class='kval'>${v}</div>${s?`<div class='delta ${cls||"flat"}'>${s}</div>`:""}</div>`;
  el.innerHTML="<div class='cards'>"+[
    card("Opus spend",fmtUSD(opus.cost),"this period"),
    card("If 100% → "+tier,fmtUSD(atTier),"−"+fmtUSD(fullDelta)+" ("+pct.toFixed(0)+"%)","down"),
    card("Savings @ "+Math.round(share*100)+"%",fmtUSD(sav),"routed to "+tier,"down"),
    card("New period total",fmtUSD(newTotal),"was "+fmtUSD(cur.cost)),
  ].join("")+"</div>";
  hint.textContent="— "+tier+" input $"+rates.input.toFixed(2)+"/1M vs Opus $"+DATA.pricing.opus.input.toFixed(2)+"/1M";
}

// ---- data health (schema drift + coverage; period-independent) ----
function renderHealth(){
  const box=$("#health"), H=DATA.health;
  if(!H){box.style.display="none";return;}
  const P=H.providers||{}, C=P.claude||{}, X=P.codex||{}, cov=C.coverage||{};
  const susp=cov.suspicious||[], gaps=cov.calendar_gaps||[];
  const stale=(cov.days_since_last_active||0)>1;
  const warn=(C.drift&&C.drift.length)||(X.drift&&X.drift.length)||susp.length||stale;
  const driftRow=(name,d)=> (d&&d.length)
    ? `<div class="hrow warn"><b>${name} schema drift (${d.length})</b> — confirm, then update <span class="mono">SCHEMA_EXPECT</span> / <span class="mono">tier()</span> in report.py<ul>${d.map(x=>`<li class="mono">${esc(x)}</li>`).join("")}</ul></div>`
    : `<div class="hrow ok">${name} schema OK <span class="hint">(${(d==null?"—":"no new fields, models, or block types")})</span></div>`;
  let h=`<div class="hhdr"><span class="hpill ${warn?"warn":"ok"}">${warn?"Needs review":"All clear"}</span><span class="hint">schema drift &amp; coverage, checked every run · generated ${esc(H.generated||"")}</span></div>`;
  h+=driftRow("Claude",C.drift);
  if(stale) h+=`<div class="hrow warn"><b>Stale:</b> no recorded usage for ${cov.days_since_last_active} day(s) — last active ${esc(cov.last||"?")}. If you used Claude Code since, a run or parse is failing.</div>`;
  if(susp.length) h+=`<div class="hrow warn"><b>${susp.length} suspicious data hole(s)</b> — had activity but nothing was recorded:<ul>${susp.slice(0,25).map(s=>`<li><b>${esc(s.date)}</b> — ${esc(s.reason)}</li>`).join("")}</ul></div>`;
  h+=`<div class="hrow ok">Coverage: <b>${cov.active_days||0}</b> active days (${esc(cov.first||"?")} → ${esc(cov.last||"?")}) · <b>${gaps.length}</b> calendar gap day(s) with no usage${gaps.length?` <span class="hint">(${esc(gaps.slice(-10).join(", "))}${gaps.length>10?" …":""})</span>`:""}. <span class="hint">Idle days are normal — the flagged holes above are the ones to check.</span></div>`;
  h+=driftRow("Codex",X.drift);
  const xs=X.scan||{}, xf=xs.flags||{};
  if(xs.files!=null) h+=`<div class="hrow ok">Codex fingerprint (no cost model yet): <b>${xs.files}</b> session files, ${fmtInt(xs.lines||0)} lines · ${xf.files_without_usage||0}/${xf.files_with_activity||0} active sessions carried <b>no</b> token_count (would cost $0)${(xs.unknown_models&&xs.unknown_models.length)?` · unmapped models: <span class="mono">${esc(xs.unknown_models.join(", "))}</span>`:""}.</div>`;
  box.innerHTML=h;
}

// ---- top sessions by cost (all time; period-independent) ----
function renderSessions(){
  const rows=DATA.sessions||[], body=$("#sesstbody");
  if(!rows.length){body.innerHTML="<tr><td colspan=8 class='muted'>No sessions.</td></tr>";return;}
  body.innerHTML=rows.map(s=>{
    const tok=tokTotal(s.tok), er=s.tool_results?s.tool_errors/s.tool_results*100:0;
    const d0=(s.start||"").slice(0,10), d1=(s.end||"").slice(0,10);
    const span=d0===d1?d0:d0+" → "+d1;
    const mix=Object.entries(s.by_model||{}).sort((a,b)=>b[1]-a[1]).map(([t,c])=>t+" "+fmtUSD(c)).join(", ");
    return `<tr><td class='mono'>${esc((s.id||"").slice(0,8))}</td><td class='hlabel'>${esc(clean(s.project||"(root)"))}</td>`+
      `<td class='mono'>${esc(span)}</td><td class='num'>${fmtInt(s.msgs)}</td><td class='num'>${fmtTok(tok)}</td>`+
      `<td class='num'>${fmtUSD(s.cost)}</td><td class='num'>${s.tool_results?er.toFixed(1)+"%":"—"}</td><td>${esc(mix)}</td></tr>`;
  }).join("");
}

function rebuild(keepEnd){
  state.periods=aggregate(state.gran);
  state.idx=keepEnd?state.periods.length-1:Math.min(state.idx,state.periods.length-1);
  render();
}

// pricing chart (static)
(function(){
  const pd=Object.keys(DATA.pricing_history).sort();
  const colors={opus:"#7338FF",sonnet:"#12C99A",haiku:"#258CF8"};
  const w=880,h=200,pad=46,n=pd.length;
  const series=["opus","sonnet","haiku"].map(m=>({m,vals:pd.map(d=>(DATA.pricing_history[d][m]||{}).input||0)}));
  const mx=Math.max(1,...series.flatMap(s=>s.vals))*1.15;
  const X=i=>pad+i*(w-2*pad)/Math.max(n-1,1),Y=v=>h-pad-(v/mx)*(h-2*pad);
  let s=`<svg viewBox='0 0 ${w} ${h}' class='chart'>`;
  for(let g=0;g<4;g++){const gy=pad+g*(h-2*pad)/3,val=mx*(1-g/3);s+=`<line x1='${pad}' y1='${gy}' x2='${w-pad}' y2='${gy}' class='grid'/><text x='${pad-6}' y='${gy+3}' class='ytick'>$${val.toFixed(1)}</text>`;}
  series.forEach(se=>{s+=`<polyline points='${se.vals.map((v,i)=>X(i)+","+Y(v)).join(" ")}' fill='none' stroke='${colors[se.m]}' stroke-width='2'/>`;
    se.vals.forEach((v,i)=>s+=`<circle cx='${X(i)}' cy='${Y(v)}' r='2.6' fill='${colors[se.m]}'/>`);});
  const step=Math.max(1,Math.ceil(n/8));for(let i=0;i<n;i+=step)s+=`<text x='${X(i)}' y='${h-pad+16}' class='xtick'>${esc(pd[i].slice(5))}</text>`;
  s+="</svg><div class='legend'>"+series.map(se=>`<span class='leg'><i style='background:${colors[se.m]}'></i>${se.m}</span>`).join("")+"</div>";
  $("#pricechart").innerHTML=s;
})();

// wire controls
document.querySelectorAll("#gran button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#gran button").forEach(x=>x.classList.remove("on"));
  b.classList.add("on"); state.gran=b.dataset.g; rebuild(true);
});
$("#metric").onchange=e=>{state.metric=e.target.value;render();};
$("#retry").oninput=()=>render();
$("#routeshare").oninput=()=>render();
$("#routetier").onchange=()=>render();
$("#prev").onclick=()=>{if(state.idx>0){state.idx--;render();}};
$("#next").onclick=()=>{if(state.idx<state.periods.length-1){state.idx++;render();}};
document.addEventListener("keydown",e=>{if(e.key==="ArrowLeft")$("#prev").click();if(e.key==="ArrowRight")$("#next").click();});

renderHealth();
renderSessions();
rebuild(true);
</script>
</body></html>"""

if __name__ == "__main__":
    # `--machine-dir` prints the resolved per-machine directory and exits, so the
    # run.cmd wrapper can target its run.log there without re-implementing the
    # hostname sanitization in batch.
    if "--machine-dir" in sys.argv:
        print(MACHINE_DIR)
    else:
        main()
