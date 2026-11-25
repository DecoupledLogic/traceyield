#!/usr/bin/env python3
"""Run extraction: one piece of delivered work as lanes, model turns and tool
calls, read straight from the provider logs.

A "run" is a time window over one Claude Code session (its main thread plus
every subagent it spawned) and, optionally, the Codex sessions that started in
a given working directory inside that window. The output is one JSON document
a reviewer's page can render: who did what, on which model and effort, how
long each tool call took, what it cost, and what each call was given and
returned.

Built on the provider layer (traceyield.providers), not on usage.db: every
lane is parsed on demand through its provider's parse_file(), so the document
is provider-blind in the same way the canonical store is, and it works on a
machine that has never run `traceyield report`. Nothing is written unless the
caller asks for a file (--out); nothing leaves the machine.

Privacy. Unlike the canonical store's default "structural" capture, this
document carries text: prompts, the model's replies, tool inputs and tool
outputs. That is the point of it, and it is why extraction is an explicit
command and never part of the scheduled report. Every text field is redacted
whole and then cut to `clip` characters, with a flag when it was cut. Values
under credential-looking names are replaced with "[redacted]"; that is a
pattern match, not a guarantee. Pass text=False (--no-text) for the same
timings and costs with nothing derived from transcript text: no prompts,
replies, inputs, outputs, call summaries or subagent descriptions.

Requests. One model request can be logged as several records that each repeat
the request's usage (Claude Code writes one record per content block). Records
are merged into one turn by request id, then message id, then record id, and
priced once.

Window. A request belongs to the window when its first record does. Its reply
text and tool calls come with it, including a call that finishes after the
window ends. A call whose request began before the window is left out.
"""
import argparse
import bisect
import json
import re
import sys

from traceyield import classification, pricing, transcripts
from traceyield.models import RawEvent, Segment, ToolCall, Turn
from traceyield.providers import ClaudeProvider, CodexProvider

DEFAULT_CLIP = 4000
REPLY_CLIP = 1200      # what the model said before acting: enough to read its intent
PROMPT_CLIP = 600

_KEYNAME = (r"[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|client[_-]?id|"
            r"authorization|credential)[A-Za-z0-9_.-]*")
_KEY = re.compile(r"(?i)^" + _KEYNAME + r"$")
# name, optional closing quote, separator, then a quoted value of any length or
# a bare value of 8+ characters (shorter bare values are mostly code, not secrets).
_SECRET = re.compile(
    r"(?i)(\b" + _KEYNAME + r"[\"']?\s*[:=]\s*)"
    r"(\"(?:[^\"\\]|\\.)+\"|'(?:[^'\\]|\\.)+'|[^\s\"',;}\]]{8,})"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}")
_BG_DONE = re.compile(r"<tool-use-id>(.*?)</tool-use-id>.*?<status>(.*?)</status>", re.S)
# Harness-written user lines that are not something the operator typed.
_NOT_OPERATOR = ("<", "Another Claude session", "This session is being continued",
                 "[Request interrupted")
_SUMMARY_KEYS = ("description", "skill", "file_path", "pattern", "query", "to", "subject")


def _mask(m):
    value = m.group(2)
    quote = value[0] if value[0] in "\"'" else ""
    return f"{m.group(1)}{quote}[redacted]{quote}"


def scrub(text):
    """Replace values that look like credentials. A pattern match, not a guarantee."""
    text = text.replace(chr(0xFFFD), "?")   # undecodable bytes in captured output
    text = _SECRET.sub(_mask, text)
    return _BEARER.sub(lambda m: f"{m.group(1)} [redacted]", text)


def _redact(value):
    """Structured arguments: replace every value held under a credential-looking key."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if isinstance(k, str) and _KEY.match(k) and v not in (None, "", False, True)
                    else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _clip(text, clip):
    """Redact the whole field, then cut it. Returns (text, was_cut)."""
    text = scrub(text or "")
    return (text, False) if len(text) <= clip else (text[:clip], True)


def _args(raw):
    """A tool call's arguments as (dict-or-None, display text)."""
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        return None, raw or ""
    parsed = _redact(parsed)
    for key in ("command", "prompt", "message"):
        if isinstance(parsed.get(key), str):
            return parsed, parsed[key]
    return parsed, json.dumps(parsed, indent=1, ensure_ascii=False)


def _summary(parsed, text):
    """One line saying what a call did, from its arguments."""
    value = ""
    if parsed:
        for key in _SUMMARY_KEYS:
            if parsed.get(key):
                value = str(parsed[key])
                if key == "file_path":
                    value = value.replace("\\", "/").split("/")[-1]
                break
    if not value:
        lines = (text or "").strip().splitlines()
        value = lines[0] if lines else ""
    return " ".join(scrub(value).split())[:200]


def _ms(a, b):
    a, b = transcripts.ms(a), transcripts.ms(b)
    return None if a is None or b is None else max(b - a, 0)


def read_lane(lane, provider, start, end, text=True, clip=DEFAULT_CLIP):
    """Parse one lane's file into (turns, calls, prompts) for the window
    [start, end] (ISO 8601 strings). See the module docstring for the request
    and window rules."""
    groups, order, group_of = {}, [], {}        # request -> merged turn; record turn_id -> request
    replies, timed_replies, calls, prompts, bg_done = {}, [], {}, [], {}
    # Codex logs a request's usage after its reply and tool calls, so an item
    # belongs to the next turn logged, not the one the provider had seen.
    usage_follows = bool(getattr(provider, "usage_follows_events", False))
    seq = 0
    for rec in provider.parse_file(lane["path"]):
        if isinstance(rec, Turn):
            key = rec.request_id or getattr(rec, "message_id", None) or rec.turn_id
            group_of[rec.turn_id] = key
            g = groups.get(key)
            if g is None:
                seq += 1
                g = groups[key] = {"id": f"{lane['id']}:{seq}", "lane": lane["id"], "t": rec.ts,
                                   "calls": [], "model": None, "effort": None}
                order.append(key)
            g["rec"] = rec                       # every record of a request repeats its usage: keep one
            g["model"] = g["model"] or rec.model or None     # a later block may omit what an earlier one stated
            g["effort"] = g["effort"] or rec.effort
        elif isinstance(rec, Segment):
            if rec.kind == "response" and rec.text:
                if usage_follows and rec.ts:
                    timed_replies.append((rec.ts, rec.text))
                else:
                    replies.setdefault(rec.turn_id, []).append(rec.text)
            elif rec.kind == "prompt" and rec.ts and isinstance(rec.text, str):
                if lane["kind"] == "main" and not rec.text.lstrip().startswith(_NOT_OPERATOR):
                    prompts.append({"t": rec.ts, "text": rec.text.strip()})
            elif rec.kind == "tool_args":
                calls.setdefault(rec.tool_call_id, {})["args"] = rec.text or ""
            elif rec.kind == "tool_output":
                calls.setdefault(rec.tool_call_id, {})["out"] = rec.text or ""
        elif isinstance(rec, ToolCall):
            c = calls.setdefault(rec.call_id, {})
            if rec.name is not None:             # the call
                c.update(id=rec.call_id, t=rec.ts, tool=rec.name, kind=rec.kind, turn=rec.turn_id)
            else:                                # its result
                c.update(end=rec.ts, ok=rec.ok, error_class=rec.error_class, exit_code=rec.exit_code)
        elif isinstance(rec, RawEvent) and rec.type == "queue-operation" and rec.raw:
            m = _BG_DONE.search(rec.raw)
            if m:
                bg_done[m.group(1)] = (rec.ts, m.group(2))

    lo, hi = transcripts.ms(start), transcripts.ms(end)

    def inside(when):
        at = transcripts.ms(when) if when else None
        return at is not None and lo is not None and hi is not None and lo <= at <= hi

    stamps = [(transcripts.ms(groups[k]["t"]) or 0, k) for k in order]
    stamps.sort()
    stamp_ms = [s[0] for s in stamps]

    def next_group(when):
        i = bisect.bisect_left(stamp_ms, transcripts.ms(when) or 0)
        return stamps[i][1] if i < len(stamps) else None

    if usage_follows:
        for when, said in timed_replies:
            key = next_group(when)
            if key is not None:
                groups[key].setdefault("said", []).append(said)

    turns, kept = [], {}
    for key in order:
        g = groups[key]
        if not inside(g["t"]):
            continue
        rec = g.pop("rec")
        said = g.pop("said", None)
        if said is None:
            said = [t for tid, k in group_of.items() if k == key for t in replies.get(tid, [])]
        reply, reply_cut = _clip("\n".join(said), min(clip, REPLY_CLIP)) if text else ("", False)
        g.update(
            provider=rec.provider, tier=rec.tier,
            tok={"in": rec.input_fresh, "out": rec.output, "read": rec.cache_read,
                 "write": rec.cache_write_5m + rec.cache_write_1h},
            priced=pricing.rate_card(rec.provider, rec.tier) is not None,
            cost=round(pricing.cost_of(rec.provider, rec.tier, rec.input_fresh, rec.output,
                                       rec.cache_read, rec.cache_write_5m, rec.cache_write_1h), 5),
            costRead=round(pricing.cost_of(rec.provider, rec.tier, 0, 0, rec.cache_read, 0, 0), 5),
            text=reply, textCut=reply_cut,
        )
        turns.append(g)
        kept[key] = g

    out_calls = []
    for c in calls.values():
        if "t" not in c:
            continue                              # a result with no call in this file
        own = c.pop("turn", None)
        key = next_group(c["t"]) if usage_follows else group_of.get(own)
        if key is not None and key not in kept:
            continue                              # its request began outside the window
        if key is None and not inside(c["t"]):
            continue
        g = kept.get(key)
        parsed, shown = _args(c.pop("args", ""))
        c.update(lane=lane["id"], turn=g["id"] if g else None,
                 summary=_summary(parsed, shown) if text else "",
                 background=bool(parsed and parsed.get("run_in_background")),
                 error=c.get("ok") is False, denied=c.get("error_class") == "user_rejected",
                 ms=_ms(c["t"], c.get("end") or c["t"]) or 0)
        done = bg_done.get(c["id"])
        if c["background"] and done:
            c["bgEnd"], c["bgMs"] = done[0], _ms(c["t"], done[0])
        output = c.pop("out", "")
        c["input"], c["inputCut"] = _clip(shown, clip) if text else ("", False)
        c["output"], c["outputCut"] = _clip(output, clip) if text else ("", False)
        c.pop("ok", None)
        if g:
            g["calls"].append(c["id"])
        out_calls.append(c)
    for g in turns:
        share = len(g["calls"]) or 1
        for cid in g["calls"]:
            calls[cid]["cost"] = round(g["cost"] / share, 5)
            calls[cid]["turnCalls"] = share
    for c in out_calls:
        c.setdefault("cost", 0.0)
        c.setdefault("turnCalls", 1)
    prompts = [p for p in prompts if inside(p["t"])]
    for p in prompts:
        p["text"], p["textCut"] = _clip(p["text"], min(clip, PROMPT_CLIP)) if text else ("", False)
    return turns, out_calls, prompts


def default_providers():
    """The real log roots. Codex tool calls logged as custom_tool_call are
    modelled here; the scheduled ingest leaves them as raw events."""
    return {"claude": ClaudeProvider(), "codex": CodexProvider(custom_tool_calls=True)}


def extract(session, start, end, cwd=None, text=True, clip=DEFAULT_CLIP, providers=None):
    """Build the run document. `providers` is {"claude": ..., "codex": ...};
    it defaults to the real log roots and exists so tests can point at
    fixtures. `cwd` adds the Codex sessions that started under it."""
    providers = providers or default_providers()
    lanes = list(providers["claude"].run_lanes(session)) if session else []
    if cwd and providers.get("codex"):
        lanes += providers["codex"].run_lanes(cwd, start, end)
    turns, calls, prompts, kept = [], [], [], []
    for lane in lanes:
        t, c, p = read_lane(lane, providers[lane["provider"]], start, end, text=text, clip=clip)
        if not t and not c and not p:
            continue                          # a lane with nothing in the window is not part of the run
        stamps = [x["t"] for x in t] + [x["t"] for x in c] + [x["t"] for x in p]
        ends = stamps + [x.get("bgEnd") or x.get("end") or x["t"] for x in c]
        lane = {k: v for k, v in lane.items() if k != "path"}
        if not text:
            lane["description"] = None        # a subagent's description is transcript text
        lane.update(
            models=sorted({x["model"] for x in t if x["model"]}),
            efforts=sorted({x["effort"] for x in t if x["effort"]}),
            turns=len(t), calls=len(c),
            errors=sum(1 for x in c if x["error"] and not x["denied"]),
            denied=sum(1 for x in c if x["denied"]),
            tok={k: sum(x["tok"][k] for x in t) for k in ("in", "out", "read", "write")},
            cost=round(sum(x["cost"] for x in t), 2), priced=all(x["priced"] for x in t) if t else False,
            start=min(stamps), end=max(ends),
        )
        kept.append(lane)
        turns += t
        calls += c
        prompts += p
    calls.sort(key=lambda x: x["t"])
    turns.sort(key=lambda x: x["t"])
    for n, c in enumerate(calls, 1):
        c["n"] = n
    rates = {}
    for prov, tier in sorted({(x["provider"], x["tier"]) for x in turns if x["priced"]}):
        rates[tier] = list(pricing.rate_card(prov, tier))
    # Failed and refused calls grouped by the report's own error taxonomy, most
    # frequent first, each with the fix the taxonomy suggests: the raw material
    # for a retrospective on the run.
    errors = {}
    for c in calls:
        if not c["error"]:
            continue
        key = c.get("error_class") or "other"
        meta = classification.ERROR_META.get(key, classification.ERROR_META["other"])
        errors.setdefault(key, {"class": key, "title": meta["title"], "fix": meta["fix"],
                                "denied": key == "user_rejected", "calls": []})["calls"].append(c["n"])
    return {
        "errors": sorted(errors.values(), key=lambda e: (-len(e["calls"]), e["class"])),
        "source": {"session": session, "codex": bool(cwd)},
        "window": {"start": start, "end": end},
        "rates": {"perMillion": rates, "cache": pricing.CACHE["claude"], "basis": "list rates, estimate"},
        "lanes": kept, "turns": turns, "calls": calls, "prompts": prompts,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="traceyield run",
        description="Extract one run (a session, its subagents and optional Codex sessions) as JSON.")
    ap.add_argument("--session", required=True, help="Claude Code session id")
    ap.add_argument("--start", required=True, help="window start, ISO 8601 UTC (2026-11-24T16:37:44Z)")
    ap.add_argument("--end", required=True, help="window end, ISO 8601 UTC")
    ap.add_argument("--cwd", help="also include Codex sessions started under this directory in the window")
    ap.add_argument("--no-text", action="store_true",
                    help="omit everything derived from transcript text: prompts, replies, inputs, outputs, summaries")
    ap.add_argument("--clip", type=int, default=DEFAULT_CLIP, help="max characters kept per text field")
    ap.add_argument("--out", help="write here; default is stdout")
    a = ap.parse_args(argv)
    if transcripts.ms(a.start) is None or transcripts.ms(a.end) is None:
        print("traceyield run: --start and --end must be ISO 8601 timestamps", file=sys.stderr)
        return 2
    doc = extract(a.session, a.start, a.end, cwd=a.cwd, text=not a.no_text, clip=a.clip)
    if not doc["lanes"]:
        print(f"traceyield run: no transcript activity for session {a.session} in that window", file=sys.stderr)
        return 2
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, ensure_ascii=False)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        json.dump(doc, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
