#!/usr/bin/env python3
"""
Deriving (days, sessions) from raw transcripts or the canonical usage.db.

This module holds the pure bucket builders (`new_tool`/`new_model`/`new_day`/
`new_session`/`_empty_bucket`), the legacy direct-parse oracle/fallback
(`analyze()`), the live SQL `GROUP BY` aggregation path (`aggregate()`, which
operates on a passed-in open sqlite connection -- NO glob/open/urllib), and
`top_sessions()`. `aggregate()` is the piece proven I/O-free: it can be
unit-tested against an in-memory sqlite connection with no filesystem or
network access (E3-F3-S1 AC2).
"""
import glob, json, os
from collections import defaultdict, Counter
from traceyield import classification, paths, pricing, transcripts

CLAUDE_PROJECTS = paths.CLAUDE_PROJECTS
project_of = transcripts.project_of
result_text = transcripts.result_text
tier = pricing.tier
cost_of = pricing.cost_of
classify = classification.classify


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
                    cost=cost_of("claude", tr, inp, out, cr, w5m, w1h)
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

def aggregate(conn, provider=None, model=None):
    """Derive (days, sessions) — the SAME shapes analyze() returns — via SQL
    GROUP BY aggregation over an open canonical-db connection (canonical.py's
    usage.db). This is the live production path (see main()); analyze() is kept
    as the equivalence oracle this is proven against (TestAggregateEquivalence
    in test_report.py) and as a resilience fallback.

    `model` scopes to one tier (e.g. 'opus'/'sonnet'/'haiku' for Claude, a
    codex_tier() label for Codex), the SAME mechanism as `provider` but keyed
    off `turn.tier` instead of `*.provider`. `model=None` (the default) adds NO
    predicate and binds NO `:model` param anywhere -- every query and all
    output stay byte-identical to before this parameter existed. A concrete
    `model` adds `AND tier = :model` to the turn-cost SELECT, and (since a
    tool_call row carries no tier of its own) an `AND t.tier = :model` on the
    tool_call queries that need it (by_tool calls/err, tool_results,
    tool_errors, errors), via a LEFT JOIN to turn keyed on
    `turn_id`+`provider` (the same join `by_tool.calls` already used for its
    day bucketing) -- a tool_call whose linked turn's tier doesn't match (or
    has no linked turn) is excluded. `provider` and `model` compose freely
    (both predicates can be active at once, for the provider×model
    intersection); when BOTH are None the function additionally nests a
    `by_model_full` facet alongside `by_provider` (see below) -- any concrete
    `provider` and/or `model` takes a scoped branch and stays lean (no
    `by_provider`/`by_model_full` added), exactly like the pre-existing
    provider-only scoping.

    `provider` controls scope:
      - `provider=None` (the default): aggregate over ALL providers, and add a
        `by_provider` facet to each day/session bucket -- a dict keyed by
        provider name, each value a FULL bucket with the SAME shape as the
        top-level day/session bucket (cost, msgs, tok, tool_results,
        tool_errors, sessions, by_model, by_project, by_tool, errors for
        days; cost, msgs, tok, tool_results, tool_errors, project, start,
        end, by_model for sessions). Each sub-bucket is produced by simply
        calling this same function scoped to that one provider (see below)
        and nesting the result -- the proven-correct scoped path, reused
        rather than re-derived. cost/msgs/tok are accumulated from the same
        tier-not-null cost loop that builds the top-level cost/msgs/tok, so
        summing `by_provider[*].cost/msgs/tok[k]` always equals the bucket's
        own top-level value. Codex has no rate card yet (cost_of("codex",
        ...) returns 0.0), so its turns still add msgs/tok at $0 cost -- the
        sums invariant holds regardless. tool_results/tool_errors/sessions/
        by_tool are NOT tier-gated and are NOT guaranteed to sum to the
        top-level value (e.g. a session that used both providers is counted
        in each provider's own scoped bucket).
      - `provider='claude'` (or any specific provider string): scope every
        query to that provider and produce output byte-identical to
        analyze() -- NO `by_provider` key is added in scoped mode. This is
        the preserved equivalence oracle (TestAggregateEquivalence). Since
        this branch is what the `provider=None` branch calls per-provider,
        passing a concrete provider string always takes this branch and
        never recurses back into the `provider=None` branch.

    Equivalence rules honored (see docs/decisions/0001-aggregate-flip.md),
    when scoped to provider='claude':
      - cost/tok/msgs/by_model/by_project/session-cost only accumulate over
        turns with tier IS NOT NULL, mirroring analyze()'s
        `if tr is None: continue` gate. Cost is recomputed from tokens at the
        CURRENT PRICING/cache_rates() — never read from a stored column;
      - day `sessions`, `tool_results`, `tool_errors`, `errors`, and
        `by_tool[*].calls`/`.err` are NOT tier-filtered (they come from
        tool_call rows / the session-id union, independent of model tier) --
        UNLESS a concrete `model` is passed, in which case `tool_results`/
        `tool_errors`/`errors`/`by_tool[*].calls`/`.err` (but NOT day
        `sessions` or session start/end/tool_results/tool_errors) gain the
        `t.tier = :model` join-filter described above;
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

    # Scope fragment: a specific provider binds ':prov' via params; None scopes
    # to "all providers" with a literal predicate and no params (avoids sqlite
    # binding errors from an unused named placeholder).
    if provider is None:
        scope, params = "provider IS NOT NULL", {}
        tc_scope = "tc.provider IS NOT NULL"
    else:
        scope, params = "provider = :prov", {"prov": provider}
        tc_scope = "tc.provider = :prov"

    # Model scope fragment -- mirrors the provider one above, but keyed off
    # `turn.tier` (a tool_call row has no tier of its own, so the tool_call
    # queries that need it join to turn). model=None adds NO predicate and
    # binds NO :model param anywhere, so every query/output stays
    # byte-identical to before this parameter existed.
    if model is None:
        turn_model_pred, tc_model_pred = "", ""
    else:
        params = dict(params, model=model)
        turn_model_pred = " AND tier = :model"
        tc_model_pred = " AND t.tier = :model"

    # project per session, for by_project / session.project
    project_of_sess = dict(conn.execute(
        f"SELECT session_id, project FROM session WHERE {scope}", params))

    # day `sessions` = distinct session_id over the UNION of turn + tool_call
    # rows on that day -- NOT tier-filtered (rule 3).
    for d, cnt in conn.execute(f"""
        SELECT day, COUNT(DISTINCT session_id) FROM (
          SELECT substr(ts,1,10) AS day, session_id FROM turn
            WHERE {scope} AND ts IS NOT NULL
          UNION
          SELECT substr(ts,1,10) AS day, session_id FROM tool_call
            WHERE {scope} AND ts IS NOT NULL
        ) GROUP BY day
    """, params):
        if d is not None:
            days[d]["sessions"] = cnt

    # tool_results / tool_errors / errors (day) and by_tool.err (day) -- NOT
    # tier-filtered (rule 3); grouped by tool_call.ts, which tracks MAX(call,
    # result) -- i.e. the RESULT's day once a result has arrived. This matches
    # analyze(), which counts a tool_result's error/tool_results/tool_errors
    # on the RESULT line's own day (a separate line from the call). A concrete
    # `model` additionally joins to the linked turn and requires its tier to
    # match (a tool_call with no linked turn, or a turn of a different tier,
    # is excluded) -- same join `by_tool.calls` below already uses.
    if model is None:
        tc_day_sql = f"""
            SELECT substr(ts,1,10), session_id, name, ok, error_class
            FROM tool_call WHERE {scope} AND ts IS NOT NULL
        """
    else:
        tc_day_sql = f"""
            SELECT substr(tc.ts,1,10), tc.session_id, tc.name, tc.ok, tc.error_class
            FROM tool_call tc LEFT JOIN turn t
              ON t.turn_id = tc.turn_id AND t.provider = tc.provider
            WHERE {tc_scope} AND tc.ts IS NOT NULL{tc_model_pred}
        """
    for day, sid, name, ok, ec in conn.execute(tc_day_sql, params):
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
    # NOTE: `t.provider = tc.provider` is the JOIN CORRELATION (always kept
    # as-is); `tc_scope` above is the separate SCOPE predicate.
    for name, turn_day, tc_day in conn.execute(f"""
        SELECT tc.name, substr(t.ts,1,10), substr(tc.ts,1,10)
        FROM tool_call tc LEFT JOIN turn t
          ON t.turn_id = tc.turn_id AND t.provider = tc.provider
        WHERE {tc_scope} AND tc.name IS NOT NULL{tc_model_pred}
    """, params):
        day = turn_day or tc_day
        if day is not None:
            days[day]["by_tool"][name]["calls"] += 1

    # session-level tool_results/tool_errors -- NOT tier-filtered (rule 7).
    for sid, ok in conn.execute(f"""
        SELECT session_id, ok FROM tool_call
        WHERE {scope} AND session_id IS NOT NULL
    """, params):
        if ok is not None:
            S = sessions[sid]
            S["tool_results"] += 1
            if ok == 0: S["tool_errors"] += 1

    # session span -- MIN/MAX(ts) over turn UNION tool_call rows (rule 7),
    # not session.first_ts/last_ts.
    for sid, lo, hi in conn.execute(f"""
        SELECT session_id, MIN(ts), MAX(ts) FROM (
          SELECT session_id, ts FROM turn WHERE {scope} AND ts IS NOT NULL
          UNION ALL
          SELECT session_id, ts FROM tool_call WHERE {scope} AND ts IS NOT NULL
        ) GROUP BY session_id
    """, params):
        S = sessions[sid]
        S["start"], S["end"] = lo, hi
        S["project"] = project_of_sess.get(sid)

    # tool_use name(s) per turn_id, for the by_tool cost/out attribution (rule 4).
    tool_names_by_turn = defaultdict(list)
    for turn_id, name in conn.execute(f"""
        SELECT turn_id, name FROM tool_call
        WHERE {scope} AND name IS NOT NULL
          AND turn_id IS NOT NULL AND turn_id != ''
    """, params):
        tool_names_by_turn[turn_id].append(name)

    # cost/tok/msgs/by_model/by_project/session-cost/by_tool(cost,out) --
    # tier-not-null turns only (rule 2). by_project keys on the TURN's OWN
    # project (per-file, populated at ingest) -- NOT the session's resolved
    # project -- so a session whose turns span two project directories still
    # splits its cost exactly like analyze() (which attributes each turn to
    # the file it was parsed from). Falls back to the session's project on
    # the (should-never-happen-for-claude) chance a turn's own project is
    # NULL. by_model/by_project are provider-blind (claude+codex share the
    # same model/project keyspaces); the by_provider facet (added only when
    # provider is None) is built separately below by recursing into this
    # same function scoped per-provider, not accumulated inline here.
    for turn_id, sid, day, tr, inp, cr, w5m, w1h, out, tproj, tprov in conn.execute(f"""
        SELECT turn_id, session_id, substr(ts,1,10), tier,
               input_fresh, cache_read, cache_write_5m, cache_write_1h, output, project, provider
        FROM turn WHERE {scope} AND tier IS NOT NULL AND ts IS NOT NULL{turn_model_pred}
    """, params):
        cost = cost_of(tprov, tr, inp, out, cr, w5m, w1h)

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

    # by_provider / by_model_full facets -- provider=None AND model=None only
    # (the fully-unscoped call). Reuses the proven-correct scoped paths:
    # aggregate(conn, provider=p) / aggregate(conn, model=m) / aggregate(conn,
    # provider=p, model=m) each return a FULL day/session bucket (same shape
    # as the top-level one) already scoped, so nesting them as-is gives every
    # panel a uniform shape to fold on client-side. Each of these recursive
    # calls passes a concrete provider and/or model string, so it always
    # takes a scoped branch above and can never recurse back into this block
    # (no infinite recursion). `by_model_full` is distinct from the existing
    # `by_model` map (which stays the lean {cost, tok} summary driving the
    # "Cost by model" bar / routing estimate) -- these are FULL buckets, one
    # per model, nested both at the top level (all providers, that model) and
    # inside each `by_provider[p]` (that provider, that model -- the
    # intersection).
    if provider is None and model is None:
        provs = sorted({r[0] for r in conn.execute(
            "SELECT DISTINCT provider FROM turn WHERE provider IS NOT NULL "
            "UNION SELECT DISTINCT provider FROM tool_call WHERE provider IS NOT NULL "
            "UNION SELECT DISTINCT provider FROM session WHERE provider IS NOT NULL")})
        models = sorted({r[0] for r in conn.execute(
            "SELECT DISTINCT tier FROM turn WHERE tier IS NOT NULL")})
        for d in out: out[d]["by_provider"] = {}; out[d]["by_model_full"] = {}
        for sid in sess: sess[sid]["by_provider"] = {}; sess[sid]["by_model_full"] = {}
        for p in provs:
            pdays, psess = aggregate(conn, provider=p)
            for d, pd in pdays.items():
                pd["by_model_full"] = {}
                out.setdefault(d, dict(pd, by_provider={}, by_model_full={}))
                out[d]["by_provider"][p] = pd
            for sid, ps in psess.items():
                ps["by_model_full"] = {}
                sess.setdefault(sid, dict(ps, by_provider={}, by_model_full={}))
                sess[sid]["by_provider"][p] = ps
            # provider x model intersection, nested under by_provider[p].
            # Skip empty (provider,model) cells -- most of the cross-product is
            # impossible (e.g. claude x a codex-only tier) and yields all-zero
            # buckets that only bloat the payload. Empty cells contribute 0 to
            # every sum, and the client's aggregate() already treats a missing
            # bucket as "no data this day", so dropping them changes nothing but
            # size (keeps the reconciliation invariant intact).
            for m in models:
                pmdays, pmsess = aggregate(conn, provider=p, model=m)
                for d, pmd in pmdays.items():
                    if _empty_bucket(pmd): continue
                    out[d]["by_provider"][p]["by_model_full"][m] = pmd
                for sid, pms in pmsess.items():
                    if _empty_bucket(pms): continue
                    sess[sid]["by_provider"][p]["by_model_full"][m] = pms
        # top-level per-model (all providers, that one model).
        for m in models:
            mdays, msess = aggregate(conn, model=m)
            for d, md in mdays.items():
                if _empty_bucket(md): continue
                out.setdefault(d, dict(md, by_provider={}, by_model_full={}))
                out[d]["by_model_full"][m] = md
            for sid, ms in msess.items():
                if _empty_bucket(ms): continue
                sess.setdefault(sid, dict(ms, by_provider={}, by_model_full={}))
                sess[sid]["by_model_full"][m] = ms
    return out, sess

def _empty_bucket(b):
    """True when a scoped day/session bucket carries no activity at all --
    zero cost, no turns, and no tool calls. Used to prune impossible
    provider x model intersection cells from the nested facet (they add only
    payload size; every sum over them is 0)."""
    return (not b.get("cost") and not b.get("msgs")
            and not b.get("tool_results") and not b.get("by_tool"))

def top_sessions(sessions, n=50):
    """Highest-cost sessions, id attached, for the report payload (full set is
    persisted to session_metrics.json; only the top N are embedded in HTML)."""
    rows = [dict(id=sid, **s) for sid, s in sessions.items()]
    return sorted(rows, key=lambda s: s["cost"], reverse=True)[:n]
