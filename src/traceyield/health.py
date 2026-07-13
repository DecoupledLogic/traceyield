#!/usr/bin/env python3
"""
Schema-drift + coverage monitoring.

The parser is resilient by design (`except: continue`), so a vendor schema
change surfaces as SILENT under-counting, not a crash — a renamed usage field
reads 0, a new model id maps to no tier and gets $0 attributed. These
functions are the observability layer that catches that: each run
fingerprints the SHAPE of the data actually seen and diffs it against a
declared baseline, and reconciles the stored day-series against the dates
transcripts still cover so drift and data holes get surfaced (stdout,
health.json, the report) instead of quietly zeroing a day. Best-effort twins
of report.check_pricing_drift(): they never raise and never change how
parsing works.

`schema_drift()` and `coverage()` are pure calculations (no I/O) -- this is
where the "pure isolated from I/O" separation (E3-F3-S1) is most visible;
`scan_claude()`/`scan_codex()` are the file-I/O fingerprinting passes that
feed them.
"""
import datetime, glob, json, os
from collections import defaultdict, Counter
from traceyield import paths, pricing

CLAUDE_PROJECTS = paths.CLAUDE_PROJECTS
CODEX_SESSIONS = paths.CODEX_SESSIONS
machine_id = paths.machine_id
tier = pricing.tier

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

def coverage(days, scan_dates, today=None, zero_cost_suspicious=True):
    """Reconcile the stored day-series against the dates transcripts still cover.
    Separates benign idle days from SUSPICIOUS holes: a date transcripts cover
    but the store never recorded, or (when `zero_cost_suspicious`) a stored day
    with tool activity yet $0 cost (usage rows silently dropped). Set
    `zero_cost_suspicious=False` for providers where a $0-cost active day is
    legitimate (e.g. Codex's recognized-but-unpriced tiers, Decision 0007 D3)
    -- the calendar-gap/corroborated-hole check still fires either way. Also a
    freshness watermark (days since the store last advanced). Scoped from the
    first active day to `today`; ancient dates whose transcripts have rotated
    away aren't actionable, so we don't reconstruct them -- we only reconcile
    within the window we can still see."""
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
            if (zero_cost_suspicious and (D.get("tool_results", 0) or 0) > 0
                    and (D.get("cost", 0) or 0) == 0):
                out["suspicious"].append({"date": ds,
                    "reason": "tool activity but $0 cost (usage rows dropped -- likely model/schema drift)"})
        cur += one
    out["days_since_last_active"] = (end - datetime.date.fromisoformat(active[-1])).days
    return out

def build_health(days, claude_fp, codex_fp):
    """Assemble the per-run health record: schema drift + coverage for both
    providers. Claude's coverage reconciles the global day-series (unchanged
    behavior). Codex's coverage is scoped to its `by_provider` facet -- a
    day's Codex-only bucket, present on days with Codex activity -- and
    suppresses the $0-cost heuristic (Codex's unpriced tiers legitimately
    cost $0; Decision 0007 D3). If no day carries a `by_provider` facet (the
    analyze() fallback path / a Claude-only machine) `codex_days` is empty
    and coverage() degrades to a benign empty record. This is the
    machine-readable artifact behind the report's Data health panel and the
    run.log warnings."""
    codex_days = {d: b["by_provider"]["codex"] for d, b in days.items()
                  if isinstance(b.get("by_provider"), dict) and "codex" in b["by_provider"]}
    return {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "machine": machine_id(),
        "providers": {
            "claude": {"scan": claude_fp, "drift": schema_drift(claude_fp, "claude"),
                       "coverage": coverage(days, claude_fp.get("dates", {}))},
            "codex": {"scan": codex_fp, "drift": schema_drift(codex_fp, "codex"),
                      "coverage": coverage(codex_days, codex_fp.get("dates", {}),
                                            zero_cost_suspicious=False)},
        },
    }

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
    for prov in ("claude", "codex"):
        cov = health["providers"][prov].get("coverage")
        if not cov: continue
        for s in cov.get("suspicious", []):
            print(f"  DATA HOLE [{prov}] {s['date']}: {s['reason']}")
        gaps = cov.get("calendar_gaps", [])
        if gaps:
            print(f"  [{prov}] {len(gaps)} calendar gap day(s) with no usage in [{cov.get('first')}..{cov.get('checked_through')}] (idle days are normal; see Data health panel)")
        dsl = cov.get("days_since_last_active")
        if dsl and dsl > 1:
            print(f"  WARNING [{prov}]: no recorded usage for {dsl} day(s) (last active {cov.get('last')})")
