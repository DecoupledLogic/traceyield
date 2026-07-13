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
"""
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
