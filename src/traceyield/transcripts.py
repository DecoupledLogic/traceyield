#!/usr/bin/env python3
"""Shared transcript helpers: project_of() / result_text() (E3-F2-S3).

Before this module existed, both of these lived in report.py (reporting),
yet canonical.py's ClaudeProvider (ingestion) needed them too and reached
across the dependency boundary with `from traceyield import report` to get
them -- the last remaining ingestion-depends-on-reporting edge (see
pricing.py's/classification.py's module docstrings for the shape of this
problem; E3-F2-S2 already extracted tier()/classify() the same way). This
module is that same fix applied to the last two: it sits BELOW both
report.py and canonical.py, has no knowledge of either, and both import it
directly.

Named `transcripts.py` (not folded into paths.py, pricing.py, or
classification.py): both functions operate on the *shape* of a transcript
line/record -- "which project directory did this file come from" and
"extract the text out of a tool_result content block" -- which is a distinct
concern from path/config resolution, rate cards, or the error taxonomy. A
new, single-purpose neutral module keeps that concern named and discoverable
rather than bolted onto an unrelated one.

Pure-ish by design: result_text() is a pure string/dict transform. project_of()
touches the filesystem only via os.path.relpath (no reads), and resolves its
default `root` through traceyield.paths.claude_projects() -- called at
call-time, not snapshotted at import time, so it stays honest even if
CLAUDE_PROJECTS is set after this module is imported (mirroring paths.py's
own "callable resolvers re-read os.environ every call" policy). This is a
pure extraction: the resolved default is byte-identical to report.py's old
`root=CLAUDE_PROJECTS` (an import-time snapshot of the same value) for the
common case where CLAUDE_PROJECTS is set once, before first use -- report.py
and canonical.py both already call project_of() with an explicit `root`, so
this only changes WHEN the default is resolved, never WHAT it resolves to.
report.py re-exports both names (report.project_of, report.result_text) for
backward compatibility with existing call sites and tests.

E3-F2-S4 adds three more shared helpers that used to live in canonical.py's
"shared helpers" section: `iter_json_lines()`, `ms()`, and `tool_kind()` (plus
its `TOOL_KIND` table). All three are consumed by BOTH `ClaudeProvider` and
`CodexProvider` (now in `traceyield.providers.claude` / `.codex`), which is
exactly the "shared, cross-provider, dependency-free" shape this module
exists for -- moving them here (rather than leaving them in canonical.py, or
duplicating them in each provider module) means a provider module never has
to `from traceyield import canonical` just to parse a JSONL line or classify
a tool name, which would be the same layering mistake the E3-F2 feature
exists to fix, just relocated. `canonical.py` re-exports all three under
their original (underscore-prefixed, for the two that were "private")
names -- `canonical._iter_json_lines`, `canonical._ms`, `canonical.tool_kind`,
`canonical.TOOL_KIND` -- as the SAME function/dict objects, not copies, so
existing call sites and tests are unaffected.
"""
import datetime
import json
import os

from traceyield import paths

def project_of(path, root=None):
    if root is None:
        root = paths.claude_projects()
    return os.path.relpath(path, root).split(os.sep)[0]

def result_text(b):
    c = b.get("content")
    if isinstance(c, str): return c
    if isinstance(c, list): return " ".join(x.get("text","") for x in c if isinstance(x, dict))
    return ""

def iter_json_lines(path):
    """Yield each non-blank line of a JSONL file as a parsed object, silently
    skipping a line (or the whole file) that can't be opened/parsed -- same
    resilient-by-design stance as the rest of the pipeline."""
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

def ms(ts):
    """ISO-8601 -> epoch milliseconds (for wall/latency deltas); None on junk."""
    if not ts: return None
    try:
        return int(datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None

# Raw tool name (either provider) -> normalized kind. Cross-provider so tool
# analysis works regardless of harness (canonical-data-model.md §4.2). Codex
# names included even though ClaudeProvider never emits them, and vice versa.
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
