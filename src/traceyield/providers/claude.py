#!/usr/bin/env python3
"""ClaudeProvider: produces neutral Recs from Claude Code transcripts
(~/.claude/projects).

Moved out of canonical.py in E3-F2-S4 (was defined there since the module's
creation); this is a pure relocation -- every line of parsing logic is
unchanged, only its home moved so canonical.py can stay the provider-blind
store/consumer and this module can depend on nothing but the neutral layer
(models, paths, pricing, classification, transcripts). See
traceyield.providers.base.Provider for the contract this satisfies
(structurally -- this class never imports that module).
"""
import json

from traceyield import classification, paths, pricing, transcripts
from traceyield.models import RawEvent, Segment, Session, ToolCall, Turn


class ClaudeProvider:
    """Produces neutral Recs from Claude Code transcripts (~/.claude/projects)."""
    name = "claude"

    def __init__(self, root=None):
        self.root = root or paths.claude_projects()

    def roots(self):
        return [self.root]

    def parse_file(self, path):
        proj = transcripts.project_of(path, self.root)
        sid = None
        meta = {}                 # cwd / git / version, first seen wins (file-level)
        idmeta = {}               # tool_use.id -> (turn_id, call_ts_ms) for the result join
        prev_ms = {}              # sessionId -> last turn ts (ms), for wall_ms
        spans = {}                 # sessionId -> [first_ts, last_ts] — PER SESSION, not per
                                    # file: one file can hold multiple sessions (a rotated/
                                    # resumed conversation), so a single file-wide span would
                                    # bleed one session's timestamps into another's.
        seq = 0
        for o in transcripts.iter_json_lines(path):
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
                cur = transcripts.ms(ts)
                wall = (cur - prev_ms[sid]) if (sid in prev_ms and cur is not None and prev_ms[sid] is not None) else None
                prev_ms[sid] = cur
                n_tools = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use")
                yield Turn("claude", sid, tid, ts, m.get("model") or "",
                           parent_turn_id=o.get("parentUuid"), request_id=o.get("requestId"),
                           stop_reason=m.get("stop_reason"), input_fresh=inp, cache_read=cr,
                           cache_write_5m=w5m, cache_write_1h=w1h, output=out,
                           reasoning_output=None,   # Claude has no separate reasoning count (§2.4)
                           n_tool_calls=n_tools, wall_ms=wall, tier=pricing.tier(m.get("model")),
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
                        yield ToolCall("claude", sid, cid, tid, ts, name=nm, kind=transcripts.tool_kind(nm))
                        yield Segment("tool_args", "tool", tool_call_id=cid, text=json.dumps(b.get("input", {})))
            else:                                          # a user line: prompts and/or tool_results
                if isinstance(m.get("content"), str) and m.get("role") == "user":
                    yield Segment("prompt", "user", turn_id=(o.get("uuid") or ""), text=m.get("content"))
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_result": continue
                    cid = b.get("tool_use_id"); txt = transcripts.result_text(b)
                    is_err = bool(b.get("is_error"))
                    tm = idmeta.get(cid)
                    cur = transcripts.ms(ts)
                    lat = (cur - tm[1]) if (tm and cur is not None and tm[1] is not None) else None
                    yield ToolCall("claude", sid, cid, tm[0] if tm else None, ts, name=None,
                                   ok=(not is_err), error_class=(classification.classify(txt) if is_err else None),
                                   output_bytes=len(txt), latency_ms=lat)
                    yield Segment("tool_output", "tool", tool_call_id=cid, text=txt)

        for s, (f_ts, l_ts) in spans.items():   # one Session row per distinct session_id
            yield Session("claude", s, project=proj, cwd=meta.get("cwd"),
                          git_branch=meta.get("git"), cli_version=meta.get("ver"),
                          first_ts=f_ts, last_ts=l_ts)
