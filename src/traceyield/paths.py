#!/usr/bin/env python3
"""Single source of truth for every writable location and env-driven config
value the pipeline resolves (E3-F2-S1).

Before this module existed, report.py and canonical.py each constructed
their own path literals (repo-root walk-up, `machines/<machine_id>/...`
joins, `~/.claude`/`~/.codex` expansions, ad hoc `os.environ.get(...)`
reads). That made "every writable location resolves through one place,
honoring env-var overrides" an informal convention rather than something
enforceable. This module IS that one place; report.py and canonical.py now
consume it instead of building paths themselves (see tests/test_paths.py's
AST guard, which fails if any other module in the package constructs a
writable path or reads one of these env vars directly).

Two layers of API, on purpose:
  * Callable resolvers (`machine_dir()`, `daily_file()`, `db_file()`, ...)
    re-read `os.environ` on every call. This is the REAL api: it lets tests
    flip `TRACEYIELD_MACHINE` / `CLAUDE_PROJECTS` / `CODEX_HOME` at runtime
    and see the new value immediately, with no reimport required -- the
    module-level-constant trap (a constant computed once at import time
    can't see an env var set afterwards).
  * Module-level constants (`HERE`, `MACHINE_DIR`, `DAILY_FILE`, ...) are
    import-time snapshots of the same resolvers, kept for backward
    compatibility with existing call sites/tests that reference them as
    plain attributes (`report.MACHINE_DIR`, `canonical.DB_FILE`, etc. all
    still resolve -- see report.py / canonical.py). New code should prefer
    the callables.

This is a pure extraction: every resolved value is byte-identical to what
report.py / canonical.py computed before this module existed, for the
default (no env override) case. The repo-root anchoring policy itself (data
lives under `<repo>/machines/<machine_id>/`, `pricing_history.json` shared
at the repo root) is unchanged here; re-pointing that policy at a
user-data/pipx-safe directory is a later story (E3-F4-S1).
"""
import os
import re
import socket

# ---------------------------------------------------------------- repo/data root
def here():
    """Repo root. This module lives at <repo>/src/traceyield/paths.py, but
    machines/ and pricing_history.json are repo-level artifacts (see
    CLAUDE.md), so this walks up three levels from this file's own
    directory to the repo root rather than anchoring to src/traceyield/."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = here()

# ---------------------------------------------------------------- read-only source roots
def claude_projects():
    """Root of Claude Code's own transcript logs. The CLAUDE_PROJECTS env
    var overrides the default ~/.claude/projects (e.g. tests point this at
    a temp dir so real transcripts are never touched)."""
    return os.environ.get("CLAUDE_PROJECTS") or os.path.expanduser(r"~/.claude/projects")

CLAUDE_PROJECTS = claude_projects()

def codex_sessions():
    """Root of Codex CLI rollout logs. The CODEX_HOME env var overrides the
    default ~/.codex/sessions, mirroring Codex's own CODEX_HOME convention
    (see docs/openai-usage-data-research.md)."""
    return os.environ.get("CODEX_HOME") or os.path.expanduser(r"~/.codex/sessions")

CODEX_SESSIONS = codex_sessions()

# ---------------------------------------------------------------- machine identity
def machine_id():
    """Identity of the machine whose transcripts we're parsing.

    Each machine has its own ~/.claude/projects, so the artifacts derived
    from it (daily_metrics/session_metrics/report.html/usage.db/run.log)
    are namespaced under machines/<machine_id>/ -- otherwise one machine's
    run would clobber another's data in the shared repo. Defaults to the
    sanitized hostname; the TRACEYIELD_MACHINE env var overrides it (e.g. to
    make a machine write into a pre-existing directory whose name doesn't
    match its hostname)."""
    raw = (os.environ.get("TRACEYIELD_MACHINE") or "").strip() or socket.gethostname() or "unknown"
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-._")
    return slug or "unknown"

# ---------------------------------------------------------------- writable locations
def machines_dir():
    return os.path.join(here(), "machines")

MACHINES_DIR = machines_dir()

def machine_dir():
    return os.path.join(machines_dir(), machine_id())

MACHINE_DIR = machine_dir()

def daily_file():
    return os.path.join(machine_dir(), "daily_metrics.json")

DAILY_FILE = daily_file()

def session_file():
    return os.path.join(machine_dir(), "session_metrics.json")

SESSION_FILE = session_file()

def out_html():
    return os.path.join(machine_dir(), "report.html")

OUT_HTML = out_html()

def health_file():
    return os.path.join(machine_dir(), "health.json")

HEALTH_FILE = health_file()

def db_file():
    """The canonical usage store (usage.db); lives alongside the other
    per-machine artifacts under machines/<machine_id>/."""
    return os.path.join(machine_dir(), "usage.db")

DB_FILE = db_file()

def pricing_file():
    """pricing_history.json is derived from the PRICING table (code), not
    from any machine's transcripts, so -- unlike the artifacts above -- it
    stays shared at the repo root rather than living under machines/<id>/."""
    return os.path.join(here(), "pricing_history.json")

PRICING_FILE = pricing_file()

# ---------------------------------------------------------------- other env-driven config
def capture_mode():
    """"structural" (default) stores only length + sha256 of prompts/
    responses/tool-io; "verbatim" (TRACEYIELD_CAPTURE=verbatim) stores raw
    text, capped at RAW_CAP bytes. See canonical.py's module docstring."""
    return os.environ.get("TRACEYIELD_CAPTURE") or "structural"

CAPTURE = capture_mode()

def raw_retention_days():
    """Age-out window (days) for raw_event.raw payloads; canonical.age_out()
    nulls out raw text older than this while keeping the row (and its
    sha256) for provenance. TRACEYIELD_RAW_RETENTION_DAYS overrides the
    90-day default."""
    return int(os.environ.get("TRACEYIELD_RAW_RETENTION_DAYS") or 90)

RAW_RETENTION_DAYS = raw_retention_days()
