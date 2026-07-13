#!/usr/bin/env python3
"""Shared tool-error taxonomy (E3-F2-S2).

Before this module existed, ERROR_RULES/classify()/ERROR_META lived in
report.py (reporting), yet canonical.py (ingestion) needed classify() too
and reached across the dependency boundary with
`from traceyield import report` to get it -- see pricing.py's module
docstring for the full rationale; this module is that same fix applied to
the error-taxonomy half of the story (pricing/classification are extracted
together but kept in separate modules since they're independent concerns).

Named `classification.py` rather than `errors.py`: the story that created it
extracts "pricing AND classification" as a pair (see the story title), and
`classify()` is a general text-classification primitive (tool-result text in
-> a taxonomy label out) that both ingestion and reporting apply to the same
kind of data for different purposes. `errors.py` would tie the module name
to today's one consumer (tool errors) rather than the classification
mechanism itself.

Pure by design: ERROR_RULES is a static table and classify() is a pure
string match over caller-supplied text. No file I/O, no network, no
knowledge of transcripts or the filesystem. report.py re-exports every name
below (report.ERROR_RULES, report.classify, report.ERROR_META) for backward
compatibility with existing call sites and tests.

This is a pure extraction: ERROR_RULES keeps its exact order (rules match
top-to-bottom, so order is load-bearing) and every rule's substrings/title/
fix text is byte-for-byte unchanged from report.py.
"""

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
