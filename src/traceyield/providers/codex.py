#!/usr/bin/env python3
"""CodexProvider: produces neutral Recs from Codex CLI rollout logs
(~/.codex/sessions).

Moved out of canonical.py in E3-F2-S4 (was defined there since E1-F1-S1);
pure relocation, along with its Codex-only helpers (`codex_tier`,
`_codex_text`, `_codex_tool_output`) -- every line of parsing logic is
unchanged, only its home moved so canonical.py can stay the provider-blind
store/consumer and this module can depend on nothing but the neutral layer
(models, paths, pricing, classification, transcripts). Note this module does
NOT import pricing.py: the Codex model-family→tier map below (`codex_tier`)
is deliberately its own thing, not `pricing.tier()` (that one is
Claude-only). See traceyield.providers.base.Provider for the contract this
satisfies (structurally -- this class never imports that module).
"""
import json

from traceyield import classification, paths, transcripts
from traceyield.models import RawEvent, Segment, Session, ToolCall, Turn

# Codex model family → tier label. NOT pricing.tier() (that's Claude-only:
# opus/sonnet/haiku). Unknown model -> None, but the turn is still recorded
# (raw model id never lost — same policy as ClaudeProvider/pricing.tier()).
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
        self.root = root or paths.codex_sessions()

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

        for o in transcripts.iter_json_lines(path):
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
                               project=None)   # codex turns now participate in report.aggregate()'s
                                                # default all-providers scope, but a codex turn's own
                                                # project stays None -- by_project falls back to the
                                                # session's resolved project for these rows
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
                cur = transcripts.ms(ts)
                call_meta[cid] = (cur_turn_id, cur)
                yield ToolCall("codex", sid, cid, cur_turn_id, ts, name=nm, kind=transcripts.tool_kind(nm))
                yield Segment("tool_args", "tool", tool_call_id=cid, text=p.get("arguments") or "")
                continue

            if t == "response_item" and pt == "function_call_output":
                cid = p.get("call_id") or "?"
                text, exit_code, success = _codex_tool_output(p.get("output"))
                failed = (exit_code not in (None, 0)) or (success is False)
                tm = call_meta.get(cid)
                cur = transcripts.ms(ts)
                lat = (cur - tm[1]) if (tm and cur is not None and tm[1] is not None) else None
                yield ToolCall("codex", sid, cid, tm[0] if tm else None, ts, name=None,
                               ok=(not failed), error_class=(classification.classify(text) if failed else None),
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
