#!/usr/bin/env python3
"""
Shared synthetic-transcript fixture builders for the test suite (E3-F3-S3).

Centralizes the Claude and Codex transcript-line builders that used to be
duplicated (with slightly diverging signatures) at the top of
tests/test_report.py and tests/test_canonical.py, plus the ingest helpers
each file built around them. Every test_*.py file should import fixtures
from here rather than defining its own copy.

Not a test_*.py file itself, so tests/test_layout.py's ast scan (which only
walks files matching that glob) does not apply to it -- but it still avoids
importing the repo-root `report`/`canonical` shims, using
`from traceyield import ...` instead, to stay consistent with the rest of
the suite.

Signatures here are the UNION of every call site across the two original
files (see each function's docstring for what was reconciled), so every
existing caller keeps working unchanged.
"""
import json
import os
import tempfile

from traceyield import canonical, report

# report.py's terse json.load(open(...)) idiom leaks file handles on CPython's
# GC schedule; callers of these helpers rely on this being pre-silenced (both
# original files did this at import time), so do it here too.
import warnings
warnings.simplefilter("ignore", ResourceWarning)


# --------------------------------------------------------------- Claude transcript builders
def line(**kw):
    """One transcript JSON line."""
    return json.dumps(kw)


def assistant(ts, sid, model, usage, tools=(), texts=(), thinking=None,
              uuid=None, parent=None):
    """An assistant message: model + usage, optional tool_use blocks, optional
    text/thinking content blocks, optional uuid/parentUuid (Claude Code's turn
    id -- set it to exercise replay dedup or parent-turn linkage).

    `tools` entries may be either a 2-tuple `(tool_use_id, name)` (as used by
    the former tests/test_report.py fixtures, which don't care about tool
    input) or a 3-tuple `(tool_use_id, name, input_dict)` (as used by the
    former tests/test_canonical.py fixtures, which inspect tool_call args).
    Both shapes are accepted so every existing call site works unchanged.
    """
    content = []
    for tool in tools:
        if len(tool) == 3:
            tid, name, inp = tool
        else:
            tid, name = tool
            inp = {}
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
    """A user message carrying a tool_result block (no usage/model)."""
    return line(timestamp=ts, sessionId=sid,
                message={"role": "user", "content": [{"type": "tool_result",
                         "tool_use_id": tool_use_id, "is_error": is_error, "content": text}]})


def prompt(ts, sid, text, uuid=None):
    """A plain user prompt line: no usage, no tool_result -- not a billable
    turn or tool touch, so it must not move a session's span or count as a
    day-active-session in either analyze() or aggregate()."""
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


def ingest_and_aggregate(root):
    """Ingest a transcript root into an in-memory canonical db, then run
    report.aggregate(conn, provider='claude') over it -- the equivalence-test
    twin of report.analyze(root). Scoped to 'claude' so the comparison stays
    byte-identical to analyze() (no by_provider facet in scoped mode)."""
    conn = canonical.open_db(":memory:")
    canonical.ingest(conn, [canonical.ClaudeProvider(root=root)])
    days, sessions = report.aggregate(conn, provider="claude")
    conn.close()
    return days, sessions


def ingest_lines(lines, project="projX", name="conv.jsonl", capture="structural"):
    """Write a transcript, ingest it, return (conn, tmpdir-keepalive)."""
    tmp = tempfile.TemporaryDirectory()
    write_transcript(tmp.name, project, name, lines)
    conn = canonical.open_db(":memory:")
    canonical.ingest(conn, [canonical.ClaudeProvider(root=tmp.name)], capture=capture)
    return conn, tmp


# --------------------------------------------------------------- Codex fixture helpers
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


def codex_file(root, name, lines):
    """Write a raw codex-shaped fixture file (used by the schema/health tests,
    which build lines via cx() rather than the structured codex_* builders
    above, since they're deliberately probing scan_codex()'s tolerance for
    novel/odd shapes)."""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, name), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def cx(**kw):
    """A raw JSON line for schema/health fixtures (deliberately not one of the
    structured codex_* builders -- see codex_file())."""
    return json.dumps(kw)
